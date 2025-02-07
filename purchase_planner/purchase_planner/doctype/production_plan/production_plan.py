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
    batch_df = batch_df.dropna(
        subset=["date", "processing_time", "formulation", "batch_size"]
    )

    # Convert date column to datetime
    try:
        batch_df["date"] = pd.to_datetime(
            batch_df["date"], dayfirst=True, errors="coerce"
        )
    except Exception as e:
        frappe.throw(f"Error converting dates: {str(e)}")

    # Identify and print invalid date rows
    invalid_dates = batch_df[batch_df["date"].isna()]
    if not invalid_dates.empty:
        frappe.throw(
            f"Invalid date rows found:\n{invalid_dates.to_string(index=False)}"
        )

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
    """

    # ----------------------------------------------------------------
    # 1) FETCH DAY-STOCK AND MATERIAL INFO
    # ----------------------------------------------------------------
    day_stock_doc = frappe.get_doc("Day Stock", stock_inventory).as_dict()
    day_stock_dict = {
        row["material_code"]: row for row in day_stock_doc.get("table_fpim", [])
    }

    # get previous production plan

    # ----------------------------------------------------------------
    # 2) PARSE & FILTER BATCHES
    # ----------------------------------------------------------------
    batches = _ensure_json_object(batches)
    required_keys = {"formulation", "date", "processing_time", "batch_size", "reactor"}
    filtered_batches = [b for b in batches if required_keys.issubset(b.keys())]
    if not filtered_batches:
        return {
            "material_requirements": [],
            "reorders": [],
            "overall_material_requirements": [],
        }

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
        materials_in_formulations.update(
            [r["material_code"] for r in f.get("ratios", [])]
        )

    existing_mat_codes = list(day_stock_dict.keys())
    combined_mat_codes = list(materials_in_formulations.union(existing_mat_codes))
    material_info_map = _fetch_material_info(combined_mat_codes)
    formulation_map = {f["formulation_id"]: f for f in formulations}

    # ----------------------------------------------------------------
    # 4) PREPARE DATA STRUCTURES FOR THE SIMULATION
    # ----------------------------------------------------------------
    current_stock = {
        mat_code: row.get("stock", 0.0) or 0.0
        for mat_code, row in day_stock_dict.items()
    }
    opening_stock = current_stock.copy()

    # Data structure to log day-by-day events
    # We'll also store "material_usage_details" for combined usage info
    daily_log = defaultdict(
        lambda: {
            "material_usage": defaultdict(float),
            "material_usage_details": defaultdict(list),
            "production_completed": defaultdict(float),
            "ending_stock": {},
        }
    )

    # Group batches by start date
    batches_by_day = defaultdict(list)
    for b in filtered_batches:
        batches_by_day[b["start_date"]].append(b)
    # ----------------------------------------------------------------
    # 5) MAIN LOOP: SIMULATE DAY-BY-DAY
    # ----------------------------------------------------------------
    current_day = min_date
    while current_day <= max_date_with_buffer:
        # 5A) Morning: Receive finished goods from *yesterday*
        _receive_finished_goods(current_day, daily_log, current_stock)
        # 5C) Process today's production batches
        if current_day in batches_by_day:
            for batch in batches_by_day[current_day]:
                _consume_materials_for_batch(
                    batch=batch,
                    current_day=current_day,
                    current_stock=current_stock,
                    formulation_map=formulation_map,
                    daily_log=daily_log,
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


def _consume_materials_for_batch(
    batch, current_day, current_stock, formulation_map, daily_log
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
        daily_log[current_day]["material_usage_details"][packaging_code].append(
            usage_context
        )

    # (C) Optionally record finished goods if your flow requires it
    # daily_log[current_day]["production_completed"][formulation_id] += actual_batch_size


def _build_output(daily_log, material_info_map, opening_stock):
    """
    Assemble final data for 'material_requirements', 'reorders', and 'overall_material_requirements'
    from daily_log. Includes usage details in a new field.
    """
    sorted_days = sorted(daily_log.keys())
    material_requirements = []
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
                    "usage_details": [],  # <-- accumulate usage context over entire horizon
                }

            material_totals[material_code]["totalUsed"] += usage

            # Combine usage details for this day
            details_for_day = day_data["material_usage_details"][material_code]
            # Also append them to the global usage_details
            material_totals[material_code]["usage_details"].extend(details_for_day)

            usage_list.append(
                {
                    "materialCode": material_code,
                    "materialName": material_name,
                    "usage": usage,
                    "usageDetails": "; ".join(
                        details_for_day
                    ),  # Combine all contexts for this material on this day
                }
            )

        material_requirements.append({"date": date_str, "materials": usage_list})


        arrived_list = []

        production_completed_list = []
        for material_code, qty in day_data["production_completed"].items():
            production_completed_list.append(
                {"materialCode": material_code, "qty": qty}
            )
    # Filter out days that have no materials usage for 'material_requirements'
    material_requirements = [x for x in material_requirements if x["materials"]]

    # Prepare overall material requirements
    # Combine usage_details for each material with semicolons
    overall_material_requirements = []
    for mat_code, totals in material_totals.items():
        details_str = "; ".join(totals["usage_details"])
        overall_material_requirements.append(
            {
                "materialCode": totals["materialCode"],
                "materialName": totals["materialName"],
                "currentStock": totals["currentStock"],
                "totalUsed": totals["totalUsed"],
                "totalReorder": totals["totalReorder"],
                "safetyStock": totals["safetyStock"],
                "usageDetails": details_str,
            }
        )

    return {
        "material_requirements": material_requirements,
        "overall_material_requirements": overall_material_requirements,
        "prev_material_requirements": get_prev_material_requirement_per_day(),
    }


# ----------------------------------------------------------------
# Example of your other existing function
# ----------------------------------------------------------------

def get_prev_material_requirement_per_day():
    last_plan = frappe.get_all(
        "Production Plan",
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )
    if last_plan:
        prev_material_requirement_per_day = frappe.get_all(
            "Material Requirement Per Day",
            filters={"parent": last_plan[0].name},
            fields=["*"],
        )
        return prev_material_requirement_per_day
    return []
    
    

@frappe.whitelist()
def get_previous_batches(stock_inventory):
    last_plan = frappe.get_all(
        "Production Plan",
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
        return [b for b in batches if b["date"] >= today],
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
