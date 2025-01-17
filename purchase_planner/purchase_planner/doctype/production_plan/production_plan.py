import json
import frappe
from frappe.website.website_generator import WebsiteGenerator
from datetime import datetime, timedelta
from collections import defaultdict


@frappe.whitelist()
def calculate_material_requirements(stock_inventory, batches):
    """
    Once-per-day reorder with lead-time, using "back-dated" orders:

    - In the morning of Day D, reorders that were scheduled to arrive on D are processed.
    - We process all usage for Day D (can go negative).
    - At the end of Day D, if final stock < safety, we place a reorder that "arrives" on Day D morning.
      place_date = D - lead_time (clamp to real_today if needed).
      So final stock is at or above safety at day-end, no 'Impossible Reorder' error is raised.
    """

    # ----------------------------------------------------------------
    # 1) GET DAY-STOCK
    # ----------------------------------------------------------------
    day_stock_doc = frappe.get_doc("Day Stock", stock_inventory).as_dict()
    # Convert the table_fpim rows into a dict: { material_code: row_dict }
    day_stock_dict = {
        row["material_code"]: row for row in day_stock_doc.get("table_fpim", [])
    }

    # ----------------------------------------------------------------
    # 2) GET MATERIAL INFO (lead_time, safety_stock, etc.)
    # ----------------------------------------------------------------
    existing_mat_codes = set(day_stock_dict.keys())
    material_records = frappe.get_all(
        "Material",
        filters={"material_code": ["in", list(existing_mat_codes)]},
        fields=[
            "material_code",
            "lead_time",
            "reorder_quantity_kg",
            "safety_stock",
            "unit_of_measure",
        ],
    )
    material_info_map = {}
    for mat in material_records:
        material_info_map[mat["material_code"]] = {
            "lead_time": mat.get("lead_time", 0) or 0,
            "reorder_qty": mat.get("reorder_quantity_kg", 0) or 0,
            "safety_stock": mat.get("safety_stock", 0) or 0,
            "uom": mat.get("unit_of_measure", "kg"),
        }

    # ----------------------------------------------------------------
    # 3) PARSE & FILTER BATCHES
    # ----------------------------------------------------------------
    batches = json.loads(batches) if isinstance(batches, str) else batches
    required_keys = {"formulation", "date", "processing_time", "batch_size", "reactor"}
    filtered_batches = [b for b in batches if required_keys.issubset(b.keys())]

    def _parse_date(d):
        return datetime.strptime(d, "%Y-%m-%d").date()

    for b in filtered_batches:
        b["start_date"] = _parse_date(b["date"])

    if not filtered_batches:
        # Nothing to process
        return {"material_requirements": [], "reorders": []}

    filtered_batches.sort(key=lambda x: x["start_date"])
    min_date = filtered_batches[0]["start_date"]
    max_date = max(b["start_date"] for b in filtered_batches)
    max_date_with_buffer = max_date + timedelta(days=30)

    real_today = datetime.today().date()  # For clamping place_date

    # ----------------------------------------------------------------
    # 4) GET FORMULATIONS
    # ----------------------------------------------------------------
    formulation_ids = [b["formulation"] for b in filtered_batches]
    formulations = get_formulations(formulation_ids)
    formulation_map = {f["formulation_id"]: f for f in formulations}

    # ----------------------------------------------------------------
    # 5) PREP SIMULATION
    # ----------------------------------------------------------------
    # current_stock
    current_stock = {}
    for mat_code, row in day_stock_dict.items():
        current_stock[mat_code] = row.get("stock", 0.0) or 0.0

    # Reactor occupancy check
    reactor_occupancy = defaultdict(set)  # {date: set(reactors)}

    # Instead of a list of tuples, store arrivals in a dict keyed by mat_code
    # reorder_arrivals[day][mat_code] = {"qty": X, "reason": "..."}
    reorder_arrivals = defaultdict(dict)

    # Logging: make 'reorders_placed' and 'reorders_arrived' dictionaries
    # so we only have 1 entry per (day, mat_code)
    simulation_log = defaultdict(
        lambda: {
            "material_usage": defaultdict(float),
            "reorders_placed": defaultdict(
                dict
            ),  # dict -> { mat_code: {"qty": Q, "reason": R} }
            "reorders_arrived": defaultdict(
                dict
            ),  # dict -> { mat_code: {"qty": Q, "reason": R} }
            "production_completed": defaultdict(float),
            "ending_stock": {},
        }
    )

    # Group batches by start date
    batches_by_day = defaultdict(list)
    for b in filtered_batches:
        batches_by_day[b["start_date"]].append(b)

    # ----------------------------------------------------------------
    # HELPER: place a "back-dated" reorder so it arrives on 'arrival_day'
    # ----------------------------------------------------------------
    def place_reorder_for(arrival_day, mat_code, needed_qty, reason=None):
        """
        We back-date the reorder so it "arrives" on arrival_day.
        place_date = arrival_day - timedelta(days=lead_time).
        If place_date < real_today, clamp it.
        We'll sum up qty if we reorder multiple times for same day/mat_code.
        """

        if mat_code not in material_info_map:
            # fallback if material wasn't in the original list
            material_info_map[mat_code] = {
                "lead_time": 5,
                "reorder_qty": needed_qty,
                "safety_stock": 0,
                "uom": "kg",
            }

        mat_info = material_info_map[mat_code]
        lead_time = mat_info.get("lead_time", 0)
        reorder_qty = mat_info.get("reorder_qty", 0)

        # If reorder_qty < needed_qty, override
        reorder_qty = max(reorder_qty, needed_qty)

        # The "place_date" in the simulation
        place_date = arrival_day - timedelta(days=lead_time)
        if place_date < real_today:
            place_date = real_today

        # A) Deduplicate in reorders_placed
        #    If we already placed an order for (place_date, mat_code),
        #    sum up qty and combine reason text
        placed_dict = simulation_log[place_date]["reorders_placed"][mat_code]
        if not placed_dict:
            placed_dict["qty"] = reorder_qty
            placed_dict["reason"] = reason or ""
        else:
            placed_dict["qty"] += reorder_qty
            placed_dict["reason"] += f"; {reason or ''}"

        # B) Deduplicate in reorder_arrivals
        #    reorder_arrivals[arrival_day][mat_code] -> sum quantity, combine reason
        if mat_code not in reorder_arrivals[arrival_day]:
            reorder_arrivals[arrival_day][mat_code] = {
                "qty": reorder_qty,
                "reason": reason or "",
            }
        else:
            reorder_arrivals[arrival_day][mat_code]["qty"] += reorder_qty
            reorder_arrivals[arrival_day][mat_code]["reason"] += f"; {reason or ''}"

    # ----------------------------------------------------------------
    # HELPER: process incoming reorders (morning)
    # ----------------------------------------------------------------
    def process_incoming_reorders(day):
        """
        Check reorder_arrivals[day] (a dict of {mat_code: {"qty":..., "reason":...}}),
        add to current_stock, log it in simulation_log[day]['reorders_arrived'].
        """
        if day in reorder_arrivals:
            for mat_code, info in reorder_arrivals[day].items():
                qty = info["qty"]
                reason = info["reason"]
                current_stock[mat_code] = current_stock.get(mat_code, 0) + qty

                # Deduplicate in simulation_log: store single record for that mat_code
                arrived_dict = simulation_log[day]["reorders_arrived"][mat_code]
                if not arrived_dict:
                    arrived_dict["qty"] = qty
                    arrived_dict["reason"] = reason
                else:
                    arrived_dict["qty"] = qty
                    arrived_dict["reason"] = f"; {reason}"

    # ----------------------------------------------------------------
    # MAIN LOOP: day-by-day
    # ----------------------------------------------------------------
    current_day = min_date
    while current_day <= max_date_with_buffer:
        # 1) Morning: Reorders arrive
        process_incoming_reorders(current_day)

        # 2) Process usage for all batches starting today
        if current_day in batches_by_day:
            for batch in batches_by_day[current_day]:
                batch_name = batch.get("name") or "UnknownBatch"
                reactor = batch.get("reactor", "UnknownReactor")
                formulation_id = batch.get("formulation")
                processing_time = batch.get("processing_time", 0)

                # Reactor scheduling
                if isinstance(processing_time, int) and processing_time > 24:
                    processing_days = processing_time // 24
                    if processing_time % 24 != 0:
                        processing_days += 1
                else:
                    processing_days = 1 if processing_time <= 24 else processing_time

                # check double-booking
                for offset in range(processing_days):
                    day_check = current_day + timedelta(days=offset)
                    if reactor in reactor_occupancy[day_check]:
                        frappe.throw(
                            f"Reactor '{reactor}' is double-booked on {day_check}.",
                            title="Scheduling Conflict",
                        )
                    reactor_occupancy[day_check].add(reactor)

                # Fetch formula info
                if formulation_id not in formulation_map:
                    frappe.throw(
                        f"Formulation '{formulation_id}' not found.",
                        title="Missing Formulation",
                    )
                form_data = formulation_map[formulation_id]
                std_batch_size = form_data.get("batch_size", 0)
                if std_batch_size <= 0:
                    frappe.throw(
                        f"Formulation '{formulation_id}' has invalid batch_size.",
                        title="Invalid Formulation",
                    )

                ratio_list = form_data.get("ratios", [])
                packaging_code = form_data.get("packaging_code")
                packaging_amt_std = form_data.get("amount_used", 0)
                actual_batch_size = float(batch.get("batch_size", 0) or 0)

                multiplier = actual_batch_size / std_batch_size if std_batch_size else 0

                # A) consume raw materials
                for item in ratio_list:
                    mat_code = item["material_code"]
                    qty_std = item.get("quantity_kg", 0) or 0
                    usage = round(qty_std * multiplier, 4)
                    if usage <= 0:
                        continue

                    if mat_code not in current_stock:
                        current_stock[mat_code] = 0.0

                    current_stock[mat_code] -= usage
                    simulation_log[current_day]["material_usage"][mat_code] += usage

                # B) consume packaging if any
                if packaging_code and packaging_amt_std > 0:
                    pkg_usage = round(packaging_amt_std * multiplier, 4)
                    if packaging_code not in current_stock:
                        current_stock[packaging_code] = 0.0

                    current_stock[packaging_code] -= pkg_usage
                    simulation_log[current_day]["material_usage"][
                        packaging_code
                    ] += pkg_usage

                # Optionally produce final product, if needed. Skipped here for clarity.

        # 3) End-of-day => ensure final stock >= safety
        #    If final_stock < safety, place a reorder (time-travel to morning).
        for mat_code, qty in list(current_stock.items()):
            safety = material_info_map.get(mat_code, {}).get("safety_stock", 0)
            lead_time = material_info_map.get(mat_code, {}).get("lead_time", 0)
            reorder_quantity = material_info_map.get(mat_code, {}).get("reorder_qty", 0)
            unit = material_info_map.get(mat_code, {}).get("uom", "kg")
            if qty < safety:
                needed = round(safety - qty, 4)
                reorder_reason = (
                    f"reorder to "
                    f"quantity at end of {current_day} = {qty} {unit}, safety={safety}, lead_time={lead_time} ,  reorder quantity={reorder_quantity} {unit}"
                )
                place_reorder_for(current_day, mat_code, needed, reason=reorder_reason)
                # Then process it immediately:
                process_incoming_reorders(current_day)

        # 4) Log final stock
        for mat_code, qty in current_stock.items():
            simulation_log[current_day]["ending_stock"][mat_code] = round(qty, 4)

        current_day += timedelta(days=1)

    # ----------------------------------------------------------------
    # 6) BUILD OUTPUT
    # ----------------------------------------------------------------
    sorted_days = sorted(simulation_log.keys())
    material_requirements = []
    reorders_list = []

    for d in sorted_days:
        date_str = d.strftime("%Y-%m-%d")
        day_data = simulation_log[d]

        usage_obj = {
            "date": date_str,
            "usage": dict(day_data["material_usage"]),
            "ending_stock": day_data["ending_stock"],
        }
        material_requirements.append(usage_obj)

        # Convert internal dicts to final output structure
        placed_dict = {}
        for mat_code, reorder_data in day_data["reorders_placed"].items():
            placed_dict[mat_code] = reorder_data

        arrived_dict = {}
        for mat_code_arr, arr_data in day_data["reorders_arrived"].items():
            arrived_dict[mat_code_arr] = arr_data

        reorder_obj = {
            "date": date_str,
            "reorders_placed": placed_dict,
            "reorders_arrived": arrived_dict,
            "production_completed": dict(day_data["production_completed"]),
        }
        reorders_list.append(reorder_obj)

    material_requirements_without_ending_stock = material_requirements.copy()

    for i in range(len(material_requirements_without_ending_stock)):
        material_requirements[i].pop("ending_stock")

    return {
        "material_requirements": material_requirements_without_ending_stock,
        "reorders": reorders_list,
    }


@frappe.whitelist()
def get_previous_batches(stock_inventory):
    """
    Example only. Adjust as needed.
    """
    last_plan = frappe.get_all(
        "Production Plan",
        filters={"stock_inventory": stock_inventory},
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )
    if last_plan:
        batches = frappe.get_all(
            "Batch Plan",
            filters={"parent": last_plan[0].name},
            fields=[
                "date",
                "reactor",
                "formulation",
                "batch_size",
                "processing_time",
                "remark",
                "marketing_person",
            ],
        )
        today = datetime.today().date()
        return [b for b in batches if b["date"] >= today]
    return []


def get_formulations(formulation_ids):
    """
    Returns a list of dicts:
       {
         'formulation_id': ...,
         'batch_size': ...,
         'packaging_code': ...,
         'amount_used': ...,
         'ratios': [ {material_code, quantity_kg}, ... ]
       }
    """
    if not formulation_ids:
        return []
    formulations = frappe.get_all(
        "Formulation",
        filters={"formulation_id": ["in", formulation_ids]},
        fields=["formulation_id", "batch_size", "packaging_code", "amount_used"],
    )
    for f in formulations:
        child_ratios = frappe.get_all(
            "formulation_ratio",
            filters={"parent": f["formulation_id"]},
            fields=["material_code", "quantity_kg"],
        )
        f["ratios"] = child_ratios
    return formulations


class ProductionPlan(WebsiteGenerator):
    pass
