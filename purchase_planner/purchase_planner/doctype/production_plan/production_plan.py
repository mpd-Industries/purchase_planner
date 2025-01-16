# Copyright (c) 2025, mpd-industries and contributors
# For license information, please see license.txt
import json
import frappe
from frappe.website.website_generator import WebsiteGenerator
from datetime import datetime



# method: "purchase_planner.purchase_planner.doctype.production_plan.production_plan.calculate_material_requirements",
#         args: {
#             stock_inventory: frm.doc.stock_inventory,
#             batches: frm.doc.batches
#         },

@frappe.whitelist()
def calculate_material_requirements(stock_inventory, batches):
    day_stock = frappe.get_doc('Day Stock', stock_inventory).as_dict()
    batches = json.loads(batches)
    # example of one PUV0915(UV)': {'name': '01946ded-ecdd-7931-9206-1b5191da20b4', 'owner': 'Administrator', 'creation': datetime.datetime(2025, 1, 16, 12, 34, 56, 694058), 'modified': datetime.datetime(2025, 1, 16, 12, 34, 56, 694058), 'modified_by': 'Administrator', 'docstatus': 0, 'idx': 296, 'material_code': 'PUV0915(UV)', 'material_name': None, 'stock': 8283.189, 'unit': 'kg'
    day_stock_dict = {row['material_code']: row for row in day_stock['table_fpim']}
    
    # for every material code in day_stock_dict, get the material code safety stock and reorder quantity
    material_codes_safety_stock = frappe.get_all('Material', filters={'material_code': ['in', list(day_stock_dict.keys())]}, fields=['material_code', 'lead_time', 'reorder_quantity_kg', 'safety_stock', 'unit_of_measure'])
    
    # [{'material_code': 'PUV0915(UV)', 'lead_time': 7, 'reorder_quantity_kg': 1000.0, 'safety_stock': 100.0, 'unit_of_measure': 'kg'}]
    
    
    print(day_stock_dict)
    # {
    #     "docstatus": 0,
    #     "doctype": "Batch Plan",
    #     "name": "new-batch-plan-uflyaxytjc",
    #     "__islocal": 1,
    #     "__unsaved": 1,
    #     "owner": "Administrator",
    #     "parent": "new-production-plan-gkjeagedbi",
    #     "parentfield": "batches",
    #     "parenttype": "Production Plan",
    #     "idx": 1,
    #     "date": "2025-01-16",
    #     "reactor": "A",
    #     "formulation": "ALK30676001",
    #     "batch_size": "9000",
    #     "processing_time": 72,
    #     "remark": null,
    #     "marketing_person": "Nitin"
    # },
    filtered_batches = [batch for batch in batches if all(key in batch for key in ['formulation', 'date', 'processing_time', 'batch_size', 'reactor'])]    # filter where batches do not contain formulation, date, processing_time, batch_size, reactor
    # [{'docstatus': 0, 'doctype': 'Batch Plan', 'name': 'new-batch-plan-uflyaxytjc', '__islocal': 1, '__unsaved': 1, 'owner': 'Administrator', 'parent': 'new-production-plan-gkjeagedbi', 'parentfield': 'batches', 'parenttype': 'Production Plan', 'idx': 1, 'date': '2025-01-16', 'reactor': 'A', 'formulation': 'ALK30676001', 'batch_size': '9000', 'processing_time': 72, 'remark': None, 'marketing_person': 'Nitin'}]
    print(filtered_batches)
    formulation_ids = [batch['formulation'] for batch in filtered_batches]
    
    print(formulation_ids)
    
    formulations = get_formulations(formulation_ids)
    # [{'formulation_id': 'ALK30676001', 'batch_size': 146.14, 'packaging_code': None, 'amount_used': 0.0, 'ratios': [{'material_code': 'FAT0061', 'quantity_kg': 48.28}, {'material_code': 'CRM0713', 'quantity_kg': 0.05}, {'material_code': 'CRM0105', 'quantity_kg': 26.83}, {'material_code': 'SRM0905', 'quantity_kg': 4.6}, {'material_code': 'CRM0203', 'quantity_kg': 18.76}, {'material_code': 'CRM0110', 'quantity_kg': 1.2}, {'material_code': 'CRM0205', 'quantity_kg': 0.28}, {'material_code': 'SRM0901', 'quantity_kg': 53.84}, {'material_code': 'CRM0769', 'quantity_kg': -7.06}]}]
    print(formulations)
    
    
    
    
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
        
        # filter where date is before today
        today = datetime.today().date()
        filtered_batches = [batch for batch in batches if batch['date'] >= today]
        
        return filtered_batches
    return []


def get_formulations(formulation_ids):
        # Fetch parent formulations
    formulations = frappe.get_all(
        'Formulation',
        filters={'formulation_id': ['in', formulation_ids]},
        fields=['formulation_id', 'batch_size', 'packaging_code', 'amount_used']
    )

    # Add child records for each formulation
    for formulation in formulations:
        # Fetch child table records for this formulation
        child_ratios = frappe.get_all(
            'formulation_ratio',
            filters={'parent': formulation['formulation_id']},
            fields=['material_code', 'quantity_kg']
        )
        # Attach child ratios to the formulation
        formulation['ratios'] = child_ratios

    return formulations


class ProductionPlan(WebsiteGenerator):
	pass

