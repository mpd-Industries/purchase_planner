# Copyright (c) 2025, mpd-industries and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import pandas as pd
import os

# frappe.call({
#                         method: 'purchase_planner.purchase_planner.doctype.day_stock.upload_stock_excel',
#                         args: {
#                             file_url: file.file_url,
#                             docname: frm.doc.name
#                         },
@frappe.whitelist()
def upload_stock_excel(file_url):
    file_path = frappe.utils.get_site_path(file_url.strip("/"))

    # Verify the file exists
    if not os.path.exists(file_path):
        frappe.throw(f"File not found: {file_path}")

    # Load the Excel file
    stock_df = pd.read_excel(
        file_path,
        sheet_name="RAW MATERIAL (MPD)",
        skiprows=10,
        usecols="A:B",
        header=None,
        names=["Tally Code", "Quantity"]
    )

    # Drop rows where Quantity is missing
    stock_df = stock_df.dropna(subset=["Quantity"])

    # Fetch all materials with Tally Codes in one query
    tally_codes = stock_df["Tally Code"].tolist()
    materials = frappe.db.get_all(
        "Material",
        filters={"tally_code": ["in", tally_codes]},
        fields=["tally_code", "material_code", "material_name", "unit_of_measure"]
    )

    # Convert the result to a dictionary for quick lookup
    material_map = {m["tally_code"]: m for m in materials}

    # Track missing materials and prepare the response data
    error_list = []
    updated_table = []

    for _, row in stock_df.iterrows():
        tally_code = row["Tally Code"]
        quantity = row["Quantity"]

        material = material_map.get(tally_code)
        if material:
            updated_table.append({
                "material_code": material["material_code"],
                "material_name": material["material_name"],
                "stock": quantity,
                "unit": material["unit_of_measure"]
            })
        else:
            # Add missing Tally Code to the error list
            error_list.append(tally_code)
            

    # Return the processed data
    error_list = [None if pd.isna(code) else code for code in error_list]
    return {
        "updated_table": updated_table,
        "error_list": error_list
    }

class DayStock(Document):
	pass