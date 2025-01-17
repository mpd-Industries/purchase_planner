import json
import frappe
from frappe.website.website_generator import WebsiteGenerator
from datetime import datetime, timedelta
from collections import defaultdict

@frappe.whitelist()
def calculate_material_requirements(stock_inventory, batches):
    """
    Simulates day-by-day consumption and reordering of materials based on planned batches.

    The logic goes as follows:
      1) In the morning, add finished goods from yesterday to the current stock.
      2) Subtract today's material usage from the current stock.
      3) If final stock is below safety at the end of the day, place a 'back-dated' purchase order
         so that it arrives on the same morning.
      4) Repeat for each day in the planning horizon.

    Returns a structure with 'material_requirements' (daily usage) and 'reorders' (purchase orders).
    """

    # ----------------------------------------------------------------
    # 1) FETCH DAY-STOCK AND MATERIAL INFO
    # ----------------------------------------------------------------
    day_stock_doc = frappe.get_doc("Day Stock", stock_inventory).as_dict()
    day_stock_dict = {row["material_code"]: row for row in day_stock_doc.get("table_fpim", [])}

    # Material data: lead_time, reorder_quantity_kg, safety_stock, etc.
    existing_mat_codes = list(day_stock_dict.keys())
    material_info_map = _fetch_material_info(existing_mat_codes)

    # ----------------------------------------------------------------
    # 2) PARSE & FILTER BATCHES
    # ----------------------------------------------------------------
    batches = _ensure_json_object(batches)
    required_keys = {"formulation", "date", "processing_time", "batch_size", "reactor"}
    filtered_batches = [b for b in batches if required_keys.issubset(b.keys())]
    if not filtered_batches:
        return {"material_requirements": [], "reorders": []}

    # Convert 'date' to datetime.date and sort
    for b in filtered_batches:
        b["start_date"] = datetime.strptime(b["date"], "%Y-%m-%d").date()
    filtered_batches.sort(key=lambda x: x["start_date"])

    # Find planning horizon
    min_date = filtered_batches[0]["start_date"]
    max_date = max(b["start_date"] for b in filtered_batches)
    max_date_with_buffer = max_date + timedelta(days=30)  # buffer if needed

    # ----------------------------------------------------------------
    # 3) GET FORMULATION DETAILS
    # ----------------------------------------------------------------
    formulation_ids = [b["formulation"] for b in filtered_batches]
    formulations = get_formulations(formulation_ids)
    formulation_map = {f["formulation_id"]: f for f in formulations}

    # ----------------------------------------------------------------
    # 4) PREPARE DATA STRUCTURES FOR THE SIMULATION
    # ----------------------------------------------------------------
    current_stock = {
        mat_code: row.get("stock", 0.0) or 0.0 for mat_code, row in day_stock_dict.items()
    }

    # Helper objects to track daily events
    reactor_occupancy = defaultdict(set)     # e.g. reactor_occupancy[date] = set(reactors)
    reorder_arrivals = defaultdict(dict)     # e.g. reorder_arrivals[date][mat_code] = {"qty": X, "reason": "..."}
    daily_log = defaultdict(lambda: {
        "material_usage": defaultdict(float),
        "reorders_placed": defaultdict(dict),
        "reorders_arrived": defaultdict(dict),
        "production_completed": defaultdict(float),
        "ending_stock": {}
    })

    # Group batches by start date
    batches_by_day = defaultdict(list)
    for b in filtered_batches:
        batches_by_day[b["start_date"]].append(b)

    # ----------------------------------------------------------------
    # 5) MAIN LOOP: SIMULATE DAY-BY-DAY
    # ----------------------------------------------------------------
    current_day = min_date
    while current_day <= max_date_with_buffer:
        # 5A) Morning: Receive finished goods from *yesterday* (if any)
        _receive_finished_goods(current_day, daily_log, current_stock)

        # 5B) Morning: Process any incoming purchase orders that arrive today
        _receive_purchase_orders(current_day, reorder_arrivals, current_stock, daily_log)

        # 5C) Process today's production batches (consumption of raw materials & packaging)
        if current_day in batches_by_day:
            for batch in batches_by_day[current_day]:
                _consume_materials_for_batch(
                    batch=batch,
                    current_day=current_day,
                    current_stock=current_stock,
                    reactor_occupancy=reactor_occupancy,
                    formulation_map=formulation_map,
                    daily_log=daily_log
                )

        # 5D) End of day: Check stock vs. safety and place + receive purchase orders if needed
        _check_and_reorder_if_needed(
            current_day=current_day,
            current_stock=current_stock,
            daily_log=daily_log,
            reorder_arrivals=reorder_arrivals,
            material_info_map=material_info_map
        )

        # 5E) Log final stock at the end of the day
        for mat_code, qty in current_stock.items():
            daily_log[current_day]["ending_stock"][mat_code] = round(qty, 4)

        # Move to the next day
        current_day += timedelta(days=1)

    # ----------------------------------------------------------------
    # 6) BUILD THE FINAL OUTPUT
    # ----------------------------------------------------------------
    return _build_output(daily_log)


# ----------------------------------------------------------------
# HELPER FUNCTIONS
# ----------------------------------------------------------------

def _fetch_material_info(mat_codes):
    """Returns a dict of material info keyed by material_code."""
    if not mat_codes:
        return {}

    material_records = frappe.get_all(
        "Material",
        filters={"material_code": ["in", mat_codes]},
        fields=[
            "material_code",
            "lead_time",
            "reorder_quantity_kg",
            "safety_stock",
            "unit_of_measure",
        ],
    )

    info_map = {}
    for mat in material_records:
        code = mat["material_code"]
        info_map[code] = {
            "lead_time": mat.get("lead_time", 0) or 0,
            "reorder_qty": mat.get("reorder_quantity_kg", 0) or 0,
            "safety_stock": mat.get("safety_stock", 0) or 0,
            "uom": mat.get("unit_of_measure", "kg"),
        }
    return info_map


def _ensure_json_object(batches):
    """Ensures 'batches' is a Python list of dicts."""
    if isinstance(batches, str):
        return json.loads(batches)
    return batches


def _receive_finished_goods(day, daily_log, current_stock):
    """
    If you have logic to add finished goods from the 'previous day' into stock, 
    you can implement it here. Currently, it's just a placeholder in case you
    want to handle finished product from prior day batches.
    """
    # Example: If you tracked production_completed from the prior day (day - 1),
    # you could add that quantity to current_stock here.
    previous_day = day - timedelta(days=1)
    if previous_day in daily_log:
        finished_items = daily_log[previous_day]["production_completed"]
        for mat_code, qty in finished_items.items():
            current_stock[mat_code] = current_stock.get(mat_code, 0) + qty


def _receive_purchase_orders(day, reorder_arrivals, current_stock, daily_log):
    """
    Check if there's any purchase order scheduled to arrive today.
    If so, add that quantity to current_stock and log it.
    """
    if day not in reorder_arrivals:
        return

    arrivals_for_day = reorder_arrivals[day]
    for mat_code, info in arrivals_for_day.items():
        qty = info["qty"]
        reason = info["reason"]

        # Add to current stock
        current_stock[mat_code] = current_stock.get(mat_code, 0) + qty

        # Log the arrival
        arrived_record = daily_log[day]["reorders_arrived"][mat_code]
        if not arrived_record:
            arrived_record["qty"] = qty
            arrived_record["reason"] = reason
        else:
            arrived_record["qty"] += qty
            arrived_record["reason"] += f"; {reason}"


def _consume_materials_for_batch(
    batch,
    current_day,
    current_stock,
    reactor_occupancy,
    formulation_map,
    daily_log
):
    """
    For the given batch, ensure no reactor conflict, consume materials, and (optionally) 
    log the production of finished goods.
    """
    batch_name = batch.get("name") or "UnknownBatch"
    reactor = batch.get("reactor", "UnknownReactor")
    formulation_id = batch.get("formulation")
    processing_time = batch.get("processing_time", 0)
    actual_batch_size = float(batch.get("batch_size", 0) or 0)

    # Determine how many calendar days the reactor is occupied
    processing_days = _calculate_processing_days(processing_time)

    # Check for reactor double-booking
    for offset in range(processing_days):
        day_check = current_day + timedelta(days=offset)
        if reactor in reactor_occupancy[day_check]:
            frappe.throw(
                f"Reactor '{reactor}' is double-booked on {day_check}.",
                title="Scheduling Conflict",
            )
        reactor_occupancy[day_check].add(reactor)

    # Fetch the formulation
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

    # Calculate usage multiplier (actual vs. standard batch size)
    multiplier = 0
    if std_batch_size > 0:
        multiplier = actual_batch_size / std_batch_size

    # (A) Consume raw materials
    ratio_list = form_data.get("ratios", [])
    for item in ratio_list:
        mat_code = item["material_code"]
        qty_std = item.get("quantity_kg", 0) or 0
        usage = round(qty_std * multiplier, 4)

        current_stock[mat_code] = current_stock.get(mat_code, 0) - usage
        daily_log[current_day]["material_usage"][mat_code] += usage

    # (B) Consume packaging if any
    packaging_code = form_data.get("packaging_code")
    packaging_amt_std = form_data.get("amount_used", 0)
    if packaging_code and packaging_amt_std > 0:
        pkg_usage = round(packaging_amt_std * multiplier, 4)
        current_stock[packaging_code] = current_stock.get(packaging_code, 0) - pkg_usage
        daily_log[current_day]["material_usage"][packaging_code] += pkg_usage

    # (C) Optionally: produce finished goods (where formulation id is the "material code")
    # If your logic says "at the end of processing_time, we have the finished good".
    # For simplicity, we'll assume it's recorded in 'production_completed' on the same day:
    # daily_log[current_day]["production_completed"][formulation_id] += actual_batch_size


def _calculate_processing_days(processing_time):
    """
    Convert processing_time (in hours) to the number of days the reactor is occupied.
    """
    if not processing_time or not isinstance(processing_time, (int, float)):
        return 1
    # If processing_time <= 24, treat it as occupying the reactor for 1 day
    days = processing_time // 24
    remainder = processing_time % 24
    return int(days + 1) if remainder > 0 else int(days)


def _check_and_reorder_if_needed(
    current_day,
    current_stock,
    daily_log,
    reorder_arrivals,
    material_info_map
):
    """
    If final stock is below safety, place an order that 'arrives' this same morning (back-dated),
    then immediately receive it so end-of-day stock meets the safety requirement.
    """
    for mat_code, qty in list(current_stock.items()):
        mat_info = material_info_map.get(mat_code, {})
        safety_stock = mat_info.get("safety_stock", 0)
        if qty < safety_stock:
            needed = round(safety_stock - qty, 4)
            reorder_reason = (
                f"Shortfall on {current_day} = {qty}, safety={safety_stock}, "
                f"lead_time={mat_info.get('lead_time', 0)}, reorder qty={mat_info.get('reorder_qty', 0)}"
            )
            _place_purchase_order(
                arrival_day=current_day,
                mat_code=mat_code,
                needed_qty=needed,
                reason=reorder_reason,
                reorder_arrivals=reorder_arrivals,
                daily_log=daily_log,
                material_info_map=material_info_map,
            )
            # Process immediately (same day morning)
    _receive_purchase_orders(current_day, reorder_arrivals, current_stock, daily_log)


def _place_purchase_order(
    arrival_day,
    mat_code,
    needed_qty,
    reason,
    reorder_arrivals,
    daily_log,
    material_info_map
):
    """
    Creates a purchase order that 'arrives' on arrival_day, effectively ensuring 
    end-of-day stock meets safety. This is back-dated by lead_time, but for simplicity, 
    we do not clamp to 'today' in this example. (You can reintroduce that logic if you wish.)
    """
    mat_info = material_info_map.get(mat_code, {})
    lead_time = mat_info.get("lead_time", 0)
    reorder_qty = max(mat_info.get("reorder_qty", 0), needed_qty)

    # The "place_date" in the simulation (if you need it for logging)
    place_date = arrival_day - timedelta(days=lead_time)

    # Log in reorders_placed
    placed_record = daily_log[place_date]["reorders_placed"][mat_code]
    if not placed_record:
        placed_record["qty"] = reorder_qty
        placed_record["reason"] = reason
    else:
        placed_record["qty"] += reorder_qty
        placed_record["reason"] += f"; {reason}"

    # Log arrival
    if mat_code not in reorder_arrivals[arrival_day]:
        reorder_arrivals[arrival_day][mat_code] = {
            "qty": reorder_qty,
            "reason": reason,
        }
    else:
        reorder_arrivals[arrival_day][mat_code]["qty"] += reorder_qty
        reorder_arrivals[arrival_day][mat_code]["reason"] += f"; {reason}"


def _build_output(daily_log):
    """
    Assemble final data for 'material_requirements' and 'reorders' from daily_log.
    We also remove the 'ending_stock' from 'material_requirements' if needed.
    """
    sorted_days = sorted(daily_log.keys())
    material_requirements = []
    reorders_list = []

    for d in sorted_days:
        date_str = d.strftime("%Y-%m-%d")
        day_data = daily_log[d]

        # Record usage and ending_stock
        print(day_data)
        material_requirements.append({
            "date": date_str,
            "usage": dict(day_data["material_usage"]),
            "ending_stock": day_data["ending_stock"]
        })

        # Record reorders placed/arrived
        placed_dict = {m: data for m, data in day_data["reorders_placed"].items()}
        arrived_dict = {m: data for m, data in day_data["reorders_arrived"].items()}
        production_completed = dict(day_data["production_completed"])

        reorders_list.append({
            "date": date_str,
            "reorders_placed": placed_dict,
            "reorders_arrived": arrived_dict,
            "production_completed": production_completed
        })

    # If you truly do NOT want 'ending_stock' in the 'material_requirements', remove it:
    # (But if you want it, just skip this step.)
    
    
    
    
    material_requirements_slim = []
    for entry in material_requirements:
        slim_entry = {
            "date": entry["date"],
            "usage": entry["usage"]
        }
        material_requirements_slim.append(slim_entry)

    return {
        "material_requirements": material_requirements_slim,
        "reorders": reorders_list,
    }


# ----------------------------------------------------------------
# Example of your other existing function
# ----------------------------------------------------------------
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
