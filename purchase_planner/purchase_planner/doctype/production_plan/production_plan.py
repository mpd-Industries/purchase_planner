import json
import frappe
from frappe.website.website_generator import WebsiteGenerator
from datetime import datetime, timedelta
from collections import defaultdict
import os
import pandas as pd

@frappe.whitelist()
def upload_batches(file_url):
    file_path = frappe.utils.get_site_path(file_url.strip("/"))

    # Verify the file exists
    if not os.path.exists(file_path):
        frappe.throw(f"File not found: {file_path}")

    # Load the Excel file
    batch_df = pd.read_excel(
        file_path,
        sheet_name="Batches",
    )

    # Drop rows where date, processing_time, formulation, batch_size are missing
    batch_df = batch_df.dropna(subset=["date", "processing_time", "formulation", "batch_size"])

    # Convert date column to datetime
    try:
        batch_df["date"] = pd.to_datetime(batch_df["date"], dayfirst=True, errors="coerce")
    except Exception as e:
        frappe.throw(f"Error converting dates: {str(e)}")

    # Identify and print invalid date rows
    invalid_dates = batch_df[batch_df["date"].isna()]
    if not invalid_dates.empty:
        frappe.throw(f"Invalid date rows found:\n{invalid_dates.to_string(index=False)}")

    # Format the date column as YYYY-MM-DD
    batch_df["date"] = batch_df["date"].dt.strftime("%Y-%m-%d")

    # Remove NaNs and prepare the response data
    batch_df = batch_df.fillna("")
    batch_list = batch_df.to_dict(orient="records")
    return batch_list

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

    Additionally, we capture a combined string of "Formulation, Date, Reactor" usage context:
      - For each material in the daily usage, we provide a `usageDetails` field (semicolon-separated).
      - For the overall material requirements, we also provide a combined `usageDetails` field across all days.

    Returns a structure with:
      - 'material_requirements': (daily usage + usageDetails)
      - 'reorders': (purchase orders placed/arrived + production_completed)
      - 'overall_material_requirements': (aggregate usage + usageDetails)
    """

    # ----------------------------------------------------------------
    # 1) FETCH DAY-STOCK AND MATERIAL INFO
    # ----------------------------------------------------------------
    day_stock_doc = frappe.get_doc("Day Stock", stock_inventory).as_dict()
    day_stock_dict = {row["material_code"]: row for row in day_stock_doc.get("table_fpim", [])}

    # ----------------------------------------------------------------
    # 2) PARSE & FILTER BATCHES
    # ----------------------------------------------------------------
    batches = _ensure_json_object(batches)
    required_keys = {"formulation", "date", "processing_time", "batch_size", "reactor"}
    filtered_batches = [b for b in batches if required_keys.issubset(b.keys())]
    if not filtered_batches:
        return {"material_requirements": [], "reorders": [], "overall_material_requirements": []}

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
    materials_in_formulations = set()
    for f in formulations:
        materials_in_formulations.update([r["material_code"] for r in f.get("ratios", [])])

    existing_mat_codes = list(day_stock_dict.keys())
    combined_mat_codes = list(materials_in_formulations.union(existing_mat_codes))
    material_info_map = _fetch_material_info(combined_mat_codes)
    formulation_map = {f["formulation_id"]: f for f in formulations}

    # ----------------------------------------------------------------
    # 4) PREPARE DATA STRUCTURES FOR THE SIMULATION
    # ----------------------------------------------------------------
    current_stock = {
        mat_code: row.get("stock", 0.0) or 0.0 for mat_code, row in day_stock_dict.items()
    }
    opening_stock = current_stock.copy()

    # Data structure to log day-by-day events
    # We'll also store "material_usage_details" for combined usage info
    daily_log = defaultdict(lambda: {
        "material_usage": defaultdict(float),
        "material_usage_details": defaultdict(list),  # <-- NEW: store usage contexts
        "reorders_placed": defaultdict(dict),
        "reorders_arrived": defaultdict(dict),
        "production_completed": defaultdict(float),
        "ending_stock": {}
    })

    # Group batches by start date
    batches_by_day = defaultdict(list)
    for b in filtered_batches:
        batches_by_day[b["start_date"]].append(b)

    # We won't strictly check reactor conflicts in this example, but you can re-enable if needed.
    reactor_occupancy = defaultdict(set)

    # ----------------------------------------------------------------
    # 5) MAIN LOOP: SIMULATE DAY-BY-DAY
    # ----------------------------------------------------------------
    current_day = min_date
    while current_day <= max_date_with_buffer:
        # 5A) Morning: Receive finished goods from *yesterday*
        _receive_finished_goods(current_day, daily_log, current_stock)

        # 5B) Morning: Process any incoming purchase orders that arrive today
        _receive_purchase_orders(current_day, reorder_arrivals={}, current_stock={}, daily_log={})  # Placeholder
        # ^ This line as-is doesn't do anything because reorder_arrivals is empty here.
        #   The actual reorder arrivals are handled in _check_and_reorder_if_needed, which calls _receive_purchase_orders
        #   again with real data. 
        #   If you intend to split arrival into the next day, you can rework the logic below.

        # 5C) Process today's production batches
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
            material_info_map=material_info_map
        )

        # 5E) Log final stock at the end of the day
        for mat_code, qty in current_stock.items():
            daily_log[current_day]["ending_stock"][mat_code] = round(qty, 4)

        # Move to the next day
        current_day += timedelta(days=1)

        # If we've passed the max_date by a good buffer, we can break early
        if current_day > max_date_with_buffer:
            break

    # ----------------------------------------------------------------
    # 6) BUILD THE FINAL OUTPUT
    # ----------------------------------------------------------------
    return _build_output(daily_log, material_info_map, opening_stock)


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
            "material_name",
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
            "material_name": mat.get("material_name", "Unknown"),
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
    implement it here. By default, this is a placeholder.
    """
    previous_day = day - timedelta(days=1)
    if previous_day in daily_log:
        finished_items = daily_log[previous_day]["production_completed"]
        for mat_code, qty in finished_items.items():
            current_stock[mat_code] = current_stock.get(mat_code, 0) + qty


def _receive_purchase_orders(day, reorder_arrivals, current_stock, daily_log):
    """
    Example placeholder. If you track reorder_arrivals day by day, you can implement it here.

    This dummy version does nothing because reorder_arrivals is empty in the current flow.
    In the _check_and_reorder_if_needed logic below, we place reorders and *immediately* 
    receive them the same day for simplicity (back-dated).
    """
    return


def _consume_materials_for_batch(
    batch,
    current_day,
    current_stock,
    reactor_occupancy,
    formulation_map,
    daily_log
):
    """
    For the given batch, consume materials and record usage details:
      "Formulation {form_id}, Date {batch_date}, Reactor {reactor}, Material {mat_code}, Quantity Used = {usage}"
    """
    batch_name = batch.get("name") or "UnknownBatch"
    reactor = batch.get("reactor", "UnknownReactor")
    formulation_id = batch.get("formulation")
    processing_time = batch.get("processing_time", 0)
    actual_batch_size = float(batch.get("batch_size", 0) or 0)
    batch_date_str = batch.get("date", str(current_day))

    # Optional: Reactor occupancy check
    # processing_days = _calculate_processing_days(processing_time)
    # for offset in range(processing_days):
    #     day_check = current_day + timedelta(days=offset)
    #     if reactor in reactor_occupancy[day_check]:
    #         frappe.throw(
    #             f"Reactor '{reactor}' is double-booked on {day_check}.",
    #             title="Scheduling Conflict",
    #         )
    #     reactor_occupancy[day_check].add(reactor)

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

    # Calculate usage multiplier
    multiplier = 0
    if std_batch_size > 0:
        multiplier = actual_batch_size / std_batch_size

    # (A) Consume raw materials
    ratio_list = form_data.get("ratios", [])
    for item in ratio_list:
        mat_code = item["material_code"]
        qty_std = item.get("quantity_kg", 0) or 0
        usage = round(qty_std * multiplier, 4)

        # Subtract from stock
        current_stock[mat_code] = current_stock.get(mat_code, 0) - usage

        # Build a usage context string that includes how much was used
        usage_context = (
            f"Formulation {formulation_id}, Date {batch_date_str}, Reactor {reactor}, "
            f"Material {mat_code}, Quantity Used = {usage}"
        )

        # Log usage quantity
        daily_log[current_day]["material_usage"][mat_code] += usage
        # Log the usage context
        daily_log[current_day]["material_usage_details"][mat_code].append(usage_context)

    # (B) Consume packaging if any
    packaging_code = form_data.get("packaging_code")
    packaging_amt_std = form_data.get("amount_used", 0)
    if packaging_code and packaging_amt_std > 0:
        pkg_usage = round(packaging_amt_std * multiplier, 4)
        current_stock[packaging_code] = current_stock.get(packaging_code, 0) - pkg_usage

        # Usage context for packaging
        usage_context = (
            f"Formulation {formulation_id}, Date {batch_date_str}, Reactor {reactor}, "
            f"Material {packaging_code}, Quantity Used = {pkg_usage}"
        )

        # Log packaging usage
        daily_log[current_day]["material_usage"][packaging_code] += pkg_usage
        # Log the usage context
        daily_log[current_day]["material_usage_details"][packaging_code].append(usage_context)

    # (C) Optionally record finished goods if your flow requires it
    # daily_log[current_day]["production_completed"][formulation_id] += actual_batch_size

def _calculate_processing_days(processing_time):
    """
    Convert processing_time (in hours) to the number of days the reactor is occupied.
    """
    if not processing_time or not isinstance(processing_time, (int, float)):
        return 1
    days = processing_time // 24
    remainder = processing_time % 24
    return int(days + 1) if remainder > 0 else int(days)


def _check_and_reorder_if_needed(
    current_day,
    current_stock,
    daily_log,
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
                daily_log=daily_log,
                material_info_map=mat_info
            )
            # Because it's 'back-dated', we receive it immediately
            current_stock[mat_code] += mat_info.get('reorder_qty', 0) or needed
            # Log the arrival
            daily_log[current_day]["reorders_arrived"][mat_code] = {
                "qty": mat_info.get('reorder_qty', 0) or needed,
                "reason": reorder_reason
            }


def _place_purchase_order(
    arrival_day,
    mat_code,
    needed_qty,
    reason,
    daily_log,
    material_info_map
):
    """
    Creates a purchase order that 'arrives' on arrival_day, effectively ensuring
    end-of-day stock meets the safety requirement (back-dated).
    """
    lead_time = material_info_map.get("lead_time", 0)
    reorder_qty = max(material_info_map.get("reorder_qty", 0), needed_qty)

    place_date = arrival_day - timedelta(days=lead_time)

    placed_record = daily_log[place_date]["reorders_placed"][mat_code]
    if not placed_record:
        placed_record["qty"] = reorder_qty
        placed_record["reason"] = reason
    else:
        placed_record["qty"] += reorder_qty
        placed_record["reason"] += f"; {reason}"


def _build_output(daily_log, material_info_map, opening_stock):
    """
    Assemble final data for 'material_requirements', 'reorders', and 'overall_material_requirements'
    from daily_log. Includes usage details in a new field.
    """
    sorted_days = sorted(daily_log.keys())
    material_requirements = []
    reorders_list = []

    # Keep track of overall usage totals and usage details
    material_totals = {}

    for d in sorted_days:
        date_str = d.strftime("%Y-%m-%d")
        day_data = daily_log[d]

        # Record material usage
        usage_list = []
        for material_code, usage in day_data["material_usage"].items():
            material_info = material_info_map.get(material_code, {})
            material_name = material_info.get("material_name", material_code)
            safety_stock = material_info.get("safety_stock", 0)

            # Update material_totals
            if material_code not in material_totals:
                material_totals[material_code] = {
                    "materialCode": material_code,
                    "materialName": material_name,
                    "currentStock": opening_stock.get(material_code, 0),
                    "totalUsed": 0,
                    "totalReorder": 0,
                    "safetyStock": safety_stock,
                    "usage_details": []  # <-- accumulate usage context over entire horizon
                }

            material_totals[material_code]["totalUsed"] += usage

            # Combine usage details for this day
            details_for_day = day_data["material_usage_details"][material_code]
            # Also append them to the global usage_details
            material_totals[material_code]["usage_details"].extend(details_for_day)

            usage_list.append({
                "materialCode": material_code,
                "materialName": material_name,
                "usage": usage,
                "usageDetails": "; ".join(details_for_day)  # Combine all contexts for this material on this day
            })

        material_requirements.append({
            "date": date_str,
            "materials": usage_list
        })

        # Record reorders placed/arrived
        placed_list = []
        for material_code, reorder_data in day_data["reorders_placed"].items():
            material_info = material_info_map.get(material_code, {})
            material_name = material_info.get("material_name", material_code)

            # If material wasn't in material_totals yet, initialize
            if material_code not in material_totals:
                material_totals[material_code] = {
                    "materialCode": material_code,
                    "materialName": material_name,
                    "currentStock": opening_stock.get(material_code, 0),
                    "totalUsed": 0,
                    "totalReorder": 0,
                    "safetyStock": material_info.get("safety_stock", 0),
                    "usage_details": []
                }

            material_totals[material_code]["totalReorder"] += reorder_data["qty"]

            placed_list.append({
                "materialCode": material_code,
                "materialName": material_name,
                "qty": reorder_data["qty"],
                "reason": reorder_data["reason"]
            })

        arrived_list = []
        for material_code, reorder_data in day_data["reorders_arrived"].items():
            arrived_list.append({
                "materialCode": material_code,
                "qty": reorder_data["qty"],
                "reason": reorder_data["reason"]
            })

        production_completed_list = []
        for material_code, qty in day_data["production_completed"].items():
            production_completed_list.append({
                "materialCode": material_code,
                "qty": qty
            })

        reorders_list.append({
            "date": date_str,
            "reorders_placed": placed_list,
            "reorders_arrived": arrived_list,
            "production_completed": production_completed_list
        })

    # Filter out days that have no materials usage for 'material_requirements'
    material_requirements = [x for x in material_requirements if x["materials"]]

    # Filter out days that have no reorders placed for 'reorders'
    reorders_list = [
        x for x in reorders_list
        if x["reorders_placed"] or x["reorders_arrived"] or x["production_completed"]
    ]

    # Prepare overall material requirements
    # Combine usage_details for each material with semicolons
    overall_material_requirements = []
    for mat_code, totals in material_totals.items():
        details_str = "; ".join(totals["usage_details"])
        overall_material_requirements.append({
            "materialCode": totals["materialCode"],
            "materialName": totals["materialName"],
            "currentStock": totals["currentStock"],
            "totalUsed": totals["totalUsed"],
            "totalReorder": totals["totalReorder"],
            "safetyStock": totals["safetyStock"],
            "usageDetails": details_str
        })

    return {
        "material_requirements": material_requirements,
        "reorders": reorders_list,
        "overall_material_requirements": overall_material_requirements,
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
