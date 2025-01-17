# Copyright (c) 2025, mpd-industries and contributors
# For license information, please see license.txt

import json
import frappe
from frappe.website.website_generator import WebsiteGenerator
from datetime import datetime, timedelta
from collections import defaultdict

@frappe.whitelist()
def calculate_material_requirements(stock_inventory, batches):
    """
    This version back-dates reorders so that they arrive in time for production.
    If the back-dated 'place_date' is before real-world today, we throw an error.
    Also logs a 'reason' if reorder is triggered by safety stock or shortfall.

    Updates:
    1) If material code (or packaging code) is missing from day stock, add it with 0 stock.
    2) Include packaging requirement (packaging_code, amount_used) in consumption & reorder logic.
    3) Reorder reason now includes:
       - Shortfall quantity
       - Formulation ID
       - Reactor code
       - Batch name
    """

    # ----------------------------------------------------------------
    # 1) GET DAY-STOCK (with fallback for missing materials)
    # ----------------------------------------------------------------
    day_stock_doc = frappe.get_doc('Day Stock', stock_inventory).as_dict()
    # Convert list of rows in 'table_fpim' to a dict keyed by material_code
    day_stock_dict = {row['material_code']: row for row in day_stock_doc['table_fpim']}

    # ----------------------------------------------------------------
    # 2) GET MATERIAL INFO (initially for those in Day Stock)
    # ----------------------------------------------------------------
    existing_mat_codes = set(day_stock_dict.keys())

    material_codes_safety_stock = frappe.get_all(
        'Material',
        filters={'material_code': ['in', list(existing_mat_codes)]},
        fields=['material_code', 'lead_time', 'reorder_quantity_kg', 'safety_stock', 'unit_of_measure']
    )

    # Build a map from material_code -> { lead_time, reorder_qty, safety_stock, uom }
    material_info_map = {}
    for mat in material_codes_safety_stock:
        material_info_map[mat['material_code']] = {
            'lead_time': mat.get('lead_time', 0) or 0,
            'reorder_qty': mat.get('reorder_quantity_kg', 0) or 0,
            'safety_stock': mat.get('safety_stock', 0) or 0,
            'uom': mat.get('unit_of_measure', 'kg')
        }

    # ----------------------------------------------------------------
    # 3) PARSE & FILTER BATCHES
    # ----------------------------------------------------------------
    batches = json.loads(batches) if isinstance(batches, str) else batches
    required_keys = {'formulation', 'date', 'processing_time', 'batch_size', 'reactor'}
    filtered_batches = [b for b in batches if required_keys.issubset(b.keys())]

    def _parse_date(d):
        return datetime.strptime(d, "%Y-%m-%d").date()

    for b in filtered_batches:
        b['start_date'] = _parse_date(b['date'])

    # ----------------------------------------------------------------
    # 4) GET FORMULATIONS (including packaging info)
    # ----------------------------------------------------------------
    formulation_ids = [b['formulation'] for b in filtered_batches]
    formulations = get_formulations(formulation_ids)
    formulation_map = {}
    for f in formulations:
        formulation_id = f['formulation_id']
        formulation_map[formulation_id] = f

    # ----------------------------------------------------------------
    # 5) PREP SIMULATION
    # ----------------------------------------------------------------
    # current_stock: track real-time stock throughout the simulation
    current_stock = {}
    for mat_code, row in day_stock_dict.items():
        current_stock[mat_code] = row.get('stock', 0.0) or 0.0

    # reactor occupancy
    reactor_occupancy = defaultdict(set)  # {date: set(reactors)}
    # reorder arrivals
    reorder_arrivals = defaultdict(list)  # {arrival_day: [(mat_code, qty, reason), ...]}

    # simulation log for each day
    simulation_log = defaultdict(lambda: {
        'material_usage': defaultdict(float),
        'reorders_placed': defaultdict(list),
        'reorders_arrived': defaultdict(list),
        'ending_stock': {}
    })

    # group batches by day
    batches_by_day = defaultdict(list)
    if filtered_batches:
        filtered_batches.sort(key=lambda x: x['start_date'])
        for b in filtered_batches:
            batches_by_day[b['start_date']].append(b)
        min_date = filtered_batches[0]['start_date']
        max_date = max(b['start_date'] for b in filtered_batches)
    else:
        # No batches => return empty
        return {
            "material_requirements": [],
            "reorders": []
        }

    max_date_with_buffer = max_date + timedelta(days=30)
    real_today = datetime.today().date()

    # ----------------------------------------------------------------
    # HELPER: place reorder so it arrives on 'arrival_day'
    # ----------------------------------------------------------------
    def place_reorder_for(arrival_day, mat_code, needed_qty, reason=None):
        """
        Back-date the reorder so that it arrives on arrival_day.
        place_date = arrival_day - lead_time
        If place_date < real_today => throw error (can't place an order in the real past).
        If lead_time=0 & reorder_qty=0 => assume 5 days lead and reorder exactly 'needed_qty'.
        """
        # Make sure material_info_map has an entry for mat_code
        # (if missing, default to zero everything)
        if mat_code not in material_info_map:
            material_info_map[mat_code] = {
                'lead_time': 5,
                'reorder_qty': needed_qty,
                'safety_stock': 0,
                'uom': 'kg'
            }

        mat_info = material_info_map[mat_code]
        lead_time = mat_info.get('lead_time', 0)
        reorder_qty = mat_info.get('reorder_qty', 0)

        if lead_time == 0 and reorder_qty == 0:
            # Use default 5 days lead
            lead_time = 5
            reorder_qty = needed_qty

        place_date = arrival_day - timedelta(days=lead_time)
        if place_date < real_today:
            frappe.throw(
                f"Cannot back-date reorder for '{mat_code}' before today. "
                f"Needed to place on {place_date}, which is in the past.",
                title="Impossible Reorder"
            )

        # Place the reorder for reorder_qty; it arrives on arrival_day
        simulation_log[place_date]['reorders_placed'][mat_code].append({
            "qty": reorder_qty,
            "reason": reason or ""
        })
        reorder_arrivals[arrival_day].append((mat_code, reorder_qty, reason or ""))

    # ----------------------------------------------------------------
    # HELPER: process arrivals (in the morning)
    # ----------------------------------------------------------------
    def process_incoming_reorders(day):
        if day in reorder_arrivals:
            for (mat_code, qty, reason) in reorder_arrivals[day]:
                current_stock[mat_code] = current_stock.get(mat_code, 0) + qty
                simulation_log[day]['reorders_arrived'][mat_code].append({
                    "qty": qty,
                    "reason": reason
                })

    # ----------------------------------------------------------------
    # MAIN LOOP: day by day
    # ----------------------------------------------------------------
    current_day = min_date
    while current_day <= max_date_with_buffer:
        # 1) reorder arrivals
        process_incoming_reorders(current_day)

        # 2) handle any batches starting today
        if current_day in batches_by_day:
            for batch in batches_by_day[current_day]:
                batch_name = batch.get('name') or "UnknownBatch"
                reactor = batch.get('reactor', "UnknownReactor")
                form_id = batch.get('formulation', "UnknownFormulation")
                processing_time = batch.get('processing_time', 0)

                # convert hours to days if needed
                if isinstance(processing_time, int) and processing_time > 24:
                    processing_days = processing_time // 24
                    if processing_time % 24 != 0:
                        processing_days += 1
                else:
                    processing_days = 1 if processing_time <= 24 else processing_time

                # check reactor availability
                for offset in range(processing_days):
                    check_date = current_day + timedelta(days=offset)
                    if reactor in reactor_occupancy[check_date]:
                        frappe.throw(
                            f"Reactor '{reactor}' is double-booked on {check_date} (Batch: {batch_name}).",
                            title="Scheduling Conflict"
                        )
                
                # reserve the reactor
                for offset in range(processing_days):
                    mark_date = current_day + timedelta(days=offset)
                    reactor_occupancy[mark_date].add(reactor)

                # fetch formulation data
                if form_id not in formulation_map:
                    frappe.throw(
                        f"Formulation '{form_id}' not found for batch '{batch_name}'.",
                        title="Missing Formulation"
                    )
                
                form_data = formulation_map[form_id]
                std_batch_size = form_data.get('batch_size') or 0
                if std_batch_size <= 0:
                    frappe.throw(
                        f"Invalid standard batch size in formulation '{form_id}'.",
                        title="Invalid Formulation"
                    )
                
                ratio_list = form_data.get('ratios', [])
                actual_batch_size = float(batch.get('batch_size', 0) or 0)
                multiplier = actual_batch_size / std_batch_size

                # ------------------------------
                # consume recipe materials
                # ------------------------------
                for item in ratio_list:
                    mat_code = item['material_code']
                    qty_per_std = item.get('quantity_kg', 0) or 0
                    usage = round(multiplier * qty_per_std, 4)
                    if usage <= 0:
                        continue

                    # If the mat_code isn't in day_stock/current_stock, add it with 0
                    if mat_code not in current_stock:
                        current_stock[mat_code] = 0.0
                        day_stock_dict[mat_code] = {'material_code': mat_code, 'stock': 0.0}

                    curr_qty = current_stock.get(mat_code, 0)
                    if curr_qty < usage:
                        shortfall = usage - curr_qty
                        # Include shortfall, batch_name, reactor, formulation in reason
                        reorder_reason = (
                            f"Shortfall {shortfall} kg for material '{mat_code}' in batch '{batch_name}' "
                            f"(reactor: {reactor}, formulation: {form_id})"
                        )
                        place_reorder_for(
                            current_day, 
                            mat_code, 
                            shortfall, 
                            reason=reorder_reason
                        )
                        # re-process arrivals for immediate effect
                        process_incoming_reorders(current_day)

                    # subtract usage
                    current_stock[mat_code] -= usage
                    simulation_log[current_day]['material_usage'][mat_code] += usage

                    # check safety stock
                    safety_stock = material_info_map.get(mat_code, {}).get('safety_stock', 0)
                    if current_stock[mat_code] < safety_stock:
                        shortfall_safety = round(safety_stock - current_stock[mat_code], 4)
                        safety_reason = (
                            f"Below safety stock shortfall {shortfall_safety} kg for material '{mat_code}' "
                            f"after batch '{batch_name}' (reactor: {reactor}, formulation: {form_id})"
                        )
                        place_reorder_for(
                            current_day, 
                            mat_code, 
                            shortfall_safety, 
                            reason=safety_reason
                        )
                        process_incoming_reorders(current_day)

                # ------------------------------
                # consume packaging (if any)
                # ------------------------------
                packaging_code = form_data.get('packaging_code')
                packaging_amt_std = form_data.get('amount_used', 0) or 0  # amount_used for standard batch
                if packaging_code and packaging_amt_std > 0:
                    packaging_usage = round(multiplier * packaging_amt_std, 4)

                    if packaging_code not in current_stock:
                        current_stock[packaging_code] = 0.0
                        day_stock_dict[packaging_code] = {'material_code': packaging_code, 'stock': 0.0}

                    curr_pkg_qty = current_stock[packaging_code]
                    if curr_pkg_qty < packaging_usage:
                        shortfall_pkg = round(packaging_usage - curr_pkg_qty, 4)
                        pkg_reason = (
                            f"Shortfall {shortfall_pkg} kg for packaging '{packaging_code}' in batch '{batch_name}' "
                            f"(reactor: {reactor}, formulation: {form_id})"
                        )
                        place_reorder_for(
                            current_day,
                            packaging_code,
                            shortfall_pkg,
                            reason=pkg_reason
                        )
                        process_incoming_reorders(current_day)

                    # consume packaging
                    current_stock[packaging_code] -= packaging_usage
                    simulation_log[current_day]['material_usage'][packaging_code] += packaging_usage

                    # check safety stock for packaging
                    safety_stock_pkg = material_info_map.get(packaging_code, {}).get('safety_stock', 0)
                    if current_stock[packaging_code] < safety_stock_pkg:
                        shortfall_safety_pkg = round(safety_stock_pkg - current_stock[packaging_code], 4)
                        pkg_safety_reason = (
                            f"Below safety stock shortfall {shortfall_safety_pkg} kg for packaging '{packaging_code}' "
                            f"after batch '{batch_name}' (reactor: {reactor}, formulation: {form_id})"
                        )
                        place_reorder_for(
                            current_day,
                            packaging_code,
                            shortfall_safety_pkg,
                            reason=pkg_safety_reason
                        )
                        process_incoming_reorders(current_day)

        # 3) end of day => record ending stock
        for mat_code, qty in current_stock.items():
            simulation_log[current_day]['ending_stock'][mat_code] = round(qty, 4)

        current_day += timedelta(days=1)

    # ----------------------------------------------------------------
    # 6) BUILD OUTPUT
    # ----------------------------------------------------------------
    sorted_days = sorted(simulation_log.keys())
    material_requirements = []
    reorders_list = []

    for d in sorted_days:
        date_str = d.strftime('%Y-%m-%d')
        day_data = simulation_log[d]

        # usage summary
        usage_obj = {
            "date": date_str,
            "usage": dict(day_data['material_usage']),
            "ending_stock": day_data['ending_stock']
        }
        material_requirements.append(usage_obj)

        # reorders placed => flatten so we have {mat_code: [ {qty, reason}, ... ]}
        placed_dict = {}
        for mat_code, reorder_list in day_data['reorders_placed'].items():
            placed_dict[mat_code] = reorder_list

        # reorders arrived => {mat_code: [ {qty, reason}, ... ]}
        arrived_dict = {}
        for mat_code_arr, arr_list in day_data['reorders_arrived'].items():
            arrived_dict[mat_code_arr] = arr_list

        reorder_obj = {
            "date": date_str,
            "reorders_placed": placed_dict,
            "reorders_arrived": arrived_dict
        }
        reorders_list.append(reorder_obj)

    return {
        "material_requirements": material_requirements,
        "reorders": reorders_list
    }

@frappe.whitelist()
def get_previous_batches(stock_inventory):
    last_plan = frappe.get_all(
        "Production Plan",
        filters={"stock_inventory": stock_inventory},
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1
    )
    if last_plan:
        batches = frappe.get_all(
            "Batch Plan",
            filters={"parent": last_plan[0].name},
            fields=["date", "reactor", "formulation", "batch_size", "processing_time", "remark", "marketing_person"]
        )
        
        today = datetime.today().date()
        filtered_batches = [
            batch for batch in batches 
            if batch['date'] >= today
        ]
        return filtered_batches
    return []

def get_formulations(formulation_ids):
    """
    Returns list of dicts: 
       {
         'formulation_id': ...,
         'batch_size': ...,
         'packaging_code': ...,
         'amount_used': ...,
         'ratios': [
           {'material_code': '...', 'quantity_kg': ...},
           ...
         ]
       }
    """
    if not formulation_ids:
        return []
    formulations = frappe.get_all(
        'Formulation',
        filters={'formulation_id': ['in', formulation_ids]},
        fields=['formulation_id', 'batch_size', 'packaging_code', 'amount_used']
    )
    for f in formulations:
        child_ratios = frappe.get_all(
            'formulation_ratio',
            filters={'parent': f['formulation_id']},
            fields=['material_code', 'quantity_kg']
        )
        f['ratios'] = child_ratios
    return formulations

class ProductionPlan(WebsiteGenerator):
    pass
