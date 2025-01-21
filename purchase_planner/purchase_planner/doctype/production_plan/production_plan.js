frappe.ui.form.on("Production Plan", {
    onload: function (frm) {
        console.log("Production Plan loaded");
        toggle_batches_table(frm);
    },

    stock_inventory: function (frm) {
        console.log("Stock Inventory changed:", frm.doc.stock_inventory);
        toggle_batches_table(frm);

        if (frm.doc.stock_inventory) {
            fetch_previous_batches(frm); // Fetch and populate previous batches
        }
    },

    production_plan_excel_sheet: function (frm) {
        console.log("Production Plan Excel Sheet changed:", frm.doc.production_plan_excel_sheet);
        if (frm.doc.production_plan_excel_sheet) {
            // frappe.msgprint("Excel sheet uploaded.");
            
            // Call backend function
            frappe.call({
                method: "purchase_planner.purchase_planner.doctype.production_plan.production_plan.upload_batches",
                args: {
                    file_url: frm.doc.production_plan_excel_sheet
                },
                callback: function(r) {
                    if (r.message) {
                        console.log("Response from backend:", r.message);
                        frm.clear_table("batches"); // Clear existing batches
                        r.message.forEach(batch => {
                            let child = frm.add_child("batches");
                            child.date = batch.date;
                            child.reactor = batch.reactor;
                            child.formulation = batch.formulation;
                            child.batch_size = batch.batch_size;
                            child.processing_time = batch.processing_time;
                            child.remark = batch.remark;
                            child.marketing_person = batch.marketing_person;
                        });
                    }
                }
            });
        }
    },

    before_save: function (frm) {

        debounce_send_batches_to_server(frm);
    },

    after_save: function (frm) {
    }
});

// Function to toggle the visibility of the "batches" table
function toggle_batches_table(frm) {
    const isStockInventorySelected = !!frm.doc.stock_inventory;
    frm.toggle_display("batches", isStockInventorySelected);
}

// Function to generate a unique route string
function generate_route() {
    const now = new Date();
    const day = String(now.getDate()).padStart(2, "0");
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const year = now.getFullYear();
    const hours = String(now.getHours()).padStart(2, "0");
    const minutes = String(now.getMinutes()).padStart(2, "0");

    return `production-plan-${day}-${month}-${year}-${hours}:${minutes}`;
}


// Function to fetch previous batches based on stock inventory
function fetch_previous_batches(frm) {
    frappe.call({
        method: "purchase_planner.purchase_planner.doctype.production_plan.production_plan.get_previous_batches",
        args: {
            stock_inventory: frm.doc.stock_inventory
        },
        callback: function (response) {
            if (response.message) {
                console.log("Previous batches fetched:", response.message);
                frm.clear_table("batches"); // Clear existing batches
                response.message.forEach(batch => {
                    let child = frm.add_child("batches");
                    child.date = batch.date;
                    child.reactor = batch.reactor;
                    child.formulation = batch.formulation;
                    child.batch_size = batch.batch_size;
                    child.processing_time = batch.processing_time;
                    child.remark = batch.remark;
                    child.marketing_person = batch.marketing_person;
                });
                frm.refresh_field("batches");
                frappe.msgprint("Previous batches have been populated.");
            }
        }
    });
}

frappe.ui.form.on("Batch Plan", {
    batches_add: function (frm) {
        console.log("Row added to batches table:", frm.doc.batches);
        debounce_send_batches_to_server(frm);
    },

    batches_remove: function (frm) {
        console.log("Row removed from batches table:", frm.doc.batches);
        debounce_send_batches_to_server(frm);
    },

    before_batches_remove: function (frm) {
        console.log("Preparing to remove a row from batches table:", frm.doc.batches);
        debounce_send_batches_to_server(frm);
    },

    batches_move: function (frm) {
        console.log("Row moved in batches table:", frm.doc.batches);
        debounce_send_batches_to_server(frm);
    },

    form_render: function (frm) {
        debounce_send_batches_to_server(frm);
    }
});

// Debounced function to send data to the server
let debounce_timer;
function debounce_send_batches_to_server(frm) {
    clearTimeout(debounce_timer);
    debounce_timer = setTimeout(() => {
        send_batches_to_server(frm);
    }, 300); // Adjust debounce interval as needed
}

// Function to send stock inventory and batches data to the server
function send_batches_to_server(frm) {
    if (!frm.doc.stock_inventory || !frm.doc.batches) {
        console.log("No stock inventory or batches to send to the server.");
        return;
    }

    console.log("Sending batches to the server:", frm.doc.batches);
    frappe.call({
        method: "purchase_planner.purchase_planner.doctype.production_plan.production_plan.calculate_material_requirements",
        args: {
            stock_inventory: frm.doc.stock_inventory,
            batches: frm.doc.batches
        },
        callback: function (response) {
            console.log("Material requirements calculated:", response.message);

            if (response.message) {
                populate_tables(frm, response.message);
            }
        }
    });
}

// Function to populate the child tables
function populate_tables(frm, response) {
    // Populate Material Requirement Per Day Table
    frm.clear_table("material_requirement_per_day");
    // [
    //     {
    //         "date": "2025-01-21",
    //         "materials": [
    //             {
    //                 "materialCode": "FAT0061",
    //                 "materialName": "Soya fatty acid Shankar Soya /Sap Oleo (FAT0061)",
    //                 "category": "Unspecified",
    //                 "usage": 336533.2022
    //             },
    //             {
    //                 "materialCode": "CRM0713",
    //                 "materialName": "Tri Phenol Phosphite(TPP) (Cristol) (CRM0713)",
    //                 "category": "Unspecified",
    //                 "usage": 346.60479999999995
    //             },
    //             {
    //                 "materialCode": "CRM0105",
    //                 "materialName": "Phthalic Anhydride (CRM0105)",
    //                 "category": "Unspecified",
    //                 "usage": 186435.0915
    //             },
    //             {
    //                 "materialCode": "SRM0905",
    //                 "materialName": "Mix Xylene (SRM0905)",
    //                 "category": "Unspecified",
    //                 "usage": 30610.7111
    //             },
    //             {
    //                 "materialCode": "CRM0203",
    //                 "materialName": "GLYCERINE WATER WHITE GRADE (CRM0203)",
    //                 "category": "Unspecified",
    //                 "usage": 130301.5279
    //             },
    //             {
    //                 "materialCode": "CRM0110",
    //                 "materialName": "Maleic Anhydride (CRM0110)",
    //                 "category": "Unspecified",
    //                 "usage": 7041.5811
    //             },
    //             {
    //                 "materialCode": "CRM0205",
    //                 "materialName": "Penta Erythritol(Tech. Grade) (CRM0205)",
    //                 "category": "Unspecified",
    //                 "usage": 1940.9872
    //             },
    //             {
    //                 "materialCode": "SRM0901",
    //                 "materialName": "MTO Slop Oil ( PX ) / Indl.Solvent (SRM0901)",
    //                 "category": "Unspecified",
    //                 "usage": 29473.108
    //             },
    //             {
    //                 "materialCode": "CRM0769",
    //                 "materialName": null,
    //                 "category": "Unspecified",
    //                 "usage": -48940.6052
    //             },
    //             {
    //                 "materialCode": "SRM0990",
    //                 "materialName": null,
    //                 "category": "Unspecified",
    //                 "usage": 210694.3336
    //             }
    //         ]
    //     }
    // ]
    response.material_requirements.forEach(item => {
        const date = item.date;
        item.materials.forEach(material => {
            let row = frm.add_child("material_requirement_per_day");
            row.date = date;
            row.material_code = material.materialCode;
            row.material_name = material.materialName;
            row.quantity_used = material.usage;
            row.batches = material.usageDetails
        });
    });
    frm.refresh_field("material_requirement_per_day");
    // [
    //     {
    //         "materialCode": "CRM0713",
    //         "materialName": "Tri Phenol Phosphite(TPP) (Cristol) (CRM0713)",
    //         "category": "Unspecified",
    //         "totalUsed": 346.60479999999995,
    //         "totalReorder": 440,
    //         "safetyStock": 500
    //     },
    //     {
    //         "materialCode": "CRM0203",
    //         "materialName": "GLYCERINE WATER WHITE GRADE (CRM0203)",
    //         "category": "Unspecified",
    //         "totalUsed": 130301.5279,
    //         "totalReorder": 110588.9151,
    //         "safetyStock": 0
    //     },
    //     {
    //         "materialCode": "SRM0901",
    //         "materialName": "MTO Slop Oil ( PX ) / Indl.Solvent (SRM0901)",
    //         "category": "Unspecified",
    //         "totalUsed": 29473.108,
    //         "totalReorder": 46161.9417,
    //         "safetyStock": 20000
    //     },
    //     {
    //         "materialCode": "PUV0602",
    //         "materialName": null,
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 1,
    //         "safetyStock": 0
    //     },
    //     {
    //         "materialCode": "SRM0990",
    //         "materialName": null,
    //         "category": "Unspecified",
    //         "totalUsed": 210694.3336,
    //         "totalReorder": 210874.3336,
    //         "safetyStock": 180
    //     },
    //     {
    //         "materialCode": "CRM0335",
    //         "materialName": "2-Phenylimidazole (CRM0335)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 25,
    //         "safetyStock": 10
    //     },
    //     {
    //         "materialCode": "CRM0330",
    //         "materialName": "Aerosil 380 Evonik (Fumed Silica) (CRM0330)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 200,
    //         "safetyStock": 200
    //     },
    //     {
    //         "materialCode": "CRM0675",
    //         "materialName": "Amine bottom Amix 1000 (CRM0675)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 3000,
    //         "safetyStock": 3000
    //     },
    //     {
    //         "materialCode": "SRM0907",
    //         "materialName": "C-IX Garasol 110 (SRM0907)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 3784.7965,
    //         "safetyStock": 0
    //     },
    //     {
    //         "materialCode": "ORM0045",
    //         "materialName": "Lauric Acid (ORM0045)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 3000,
    //         "safetyStock": 3000
    //     },
    //     {
    //         "materialCode": "CRM0460",
    //         "materialName": "Light Magnesium Oxide(Lt. MGO) (CRM0460)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 50,
    //         "safetyStock": 50
    //     },
    //     {
    //         "materialCode": "CRM0110",
    //         "materialName": "Maleic Anhydride (CRM0110)",
    //         "category": "Unspecified",
    //         "totalUsed": 7041.5811,
    //         "totalReorder": 7350.5048,
    //         "safetyStock": 500
    //     },
    //     {
    //         "materialCode": "SRM0905",
    //         "materialName": "Mix Xylene (SRM0905)",
    //         "category": "Unspecified",
    //         "totalUsed": 30610.7111,
    //         "totalReorder": 36511.7791,
    //         "safetyStock": 5000
    //     },
    //     {
    //         "materialCode": "CRM0140",
    //         "materialName": "OXALIC  ACID (CRM0140)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 20,
    //         "safetyStock": 20
    //     },
    //     {
    //         "materialCode": "CRM0315",
    //         "materialName": "PERA TERTIARY BUTYL PHENOL(PTBP) (CRM0315)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 10000,
    //         "safetyStock": 2500
    //     },
    //     {
    //         "materialCode": "CRM0105",
    //         "materialName": "Phthalic Anhydride (CRM0105)",
    //         "category": "Unspecified",
    //         "totalUsed": 186435.0915,
    //         "totalReorder": 193026.8905,
    //         "safetyStock": 6000
    //     },
    //     {
    //         "materialCode": "CRM0103(UV)",
    //         "materialName": "Hypophosphrous Acid (CRM0103)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 0.3821,
    //         "safetyStock": 0
    //     },
    //     {
    //         "materialCode": "CRM0588(UV)",
    //         "materialName": "SV CAT 102 (Butyltin tris {2-ethylhexoate}) (CRM0588)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 0.718,
    //         "safetyStock": 0
    //     },
    //     {
    //         "materialCode": "CRM0133",
    //         "materialName": "CRM0133 (Sulphuric Acid Technical 50 %)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 500,
    //         "safetyStock": 500
    //     },
    //     {
    //         "materialCode": "CRM0707(UV)",
    //         "materialName": "Sodium Hydroxide (Caustic Lye) 48 % Solution (CRM0707)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 410,
    //         "safetyStock": 0
    //     },
    //     {
    //         "materialCode": "FAT0061",
    //         "materialName": "Soya fatty acid Shankar Soya /Sap Oleo (FAT0061)",
    //         "category": "Unspecified",
    //         "totalUsed": 336533.2022,
    //         "totalReorder": 300250.459,
    //         "safetyStock": 5000
    //     },
    //     {
    //         "materialCode": "CRM0205",
    //         "materialName": "Penta Erythritol(Tech. Grade) (CRM0205)",
    //         "category": "Unspecified",
    //         "totalUsed": 1940.9872,
    //         "totalReorder": 0,
    //         "safetyStock": 1000
    //     },
    //     {
    //         "materialCode": "CRM0769",
    //         "materialName": null,
    //         "category": "Unspecified",
    //         "totalUsed": -48940.6052,
    //         "totalReorder": 0,
    //         "safetyStock": 0
    //     },
    //     {
    //         "materialCode": "CRM0705",
    //         "materialName": "CRM0705 (Caustic Soda Flakes)",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 500,
    //         "safetyStock": 300
    //     },
    //     {
    //         "materialCode": "ORM2009",
    //         "materialName": null,
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 5000,
    //         "safetyStock": 5000
    //     },
    //     {
    //         "materialCode": "PUV0904(UV)",
    //         "materialName": "PUV0904 (P.T.S.A. (Para Toulene Sulphonic Acid))",
    //         "category": "Unspecified",
    //         "totalUsed": 0,
    //         "totalReorder": 0.333,
    //         "safetyStock": 0
    //     }
    // ]
    // Populate Overall Materials Requirement Table
    frm.clear_table("overall_materials_requirement");
    response.overall_material_requirements.forEach(item => {
        let row = frm.add_child("overall_materials_requirement");
        row.material_code = item.materialCode;
        row.material_name = item.materialName;
        row.current_stock = item.currentStock;
        row.total_quantity = item.totalUsed;
        row.total_reorder_quantity = item.totalReorder;
        row.safety_stock = item.safetyStock;
        row.batches = item.usageDetails
    });
    frm.refresh_field("overall_materials_requirement");

    // Populate Purchase Actions Table
    frm.clear_table("purchase_actions");
    // [
    //     {
    //         "date": "2025-01-11",
    //         "reorders_placed": [
    //             {
    //                 "materialCode": "CRM0713",
    //                 "materialName": "Tri Phenol Phosphite(TPP) (Cristol) (CRM0713)",
    //                 "qty": 440,
    //                 "reason": "Shortfall on 2025-01-21 = 280.87790000000007, safety=500.0, lead_time=10, reorder qty=440.0"
    //             }
    //         ],
    //         "reorders_arrived": [],
    //         "production_completed": []
    //     },
    //     {
    //         "date": "2025-01-14",
    //         "reorders_placed": [
    //             {
    //                 "materialCode": "CRM0203",
    //                 "materialName": "GLYCERINE WATER WHITE GRADE (CRM0203)",
    //                 "qty": 110588.9151,
    //                 "reason": "Shortfall on 2025-01-21 = -110588.9151, safety=0, lead_time=7, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "SRM0901",
    //                 "materialName": "MTO Slop Oil ( PX ) / Indl.Solvent (SRM0901)",
    //                 "qty": 46161.9417,
    //                 "reason": "Shortfall on 2025-01-21 = -26161.9417, safety=20000.0, lead_time=7, reorder qty=5000.0"
    //             },
    //             {
    //                 "materialCode": "PUV0602",
    //                 "materialName": null,
    //                 "qty": 1,
    //                 "reason": "Shortfall on 2025-01-21 = -1.0, safety=0, lead_time=7, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "SRM0990",
    //                 "materialName": null,
    //                 "qty": 210874.3336,
    //                 "reason": "Shortfall on 2025-01-21 = -210694.3336, safety=180.0, lead_time=7, reorder qty=180.0"
    //             }
    //         ],
    //         "reorders_arrived": [],
    //         "production_completed": []
    //     },
    //     {
    //         "date": "2025-01-16",
    //         "reorders_placed": [
    //             {
    //                 "materialCode": "CRM0335",
    //                 "materialName": "2-Phenylimidazole (CRM0335)",
    //                 "qty": 25,
    //                 "reason": "Shortfall on 2025-01-21 = 9.2274, safety=10.0, lead_time=5, reorder qty=25.0"
    //             },
    //             {
    //                 "materialCode": "CRM0330",
    //                 "materialName": "Aerosil 380 Evonik (Fumed Silica) (CRM0330)",
    //                 "qty": 200,
    //                 "reason": "Shortfall on 2025-01-21 = 0.0003, safety=200.0, lead_time=5, reorder qty=200.0"
    //             },
    //             {
    //                 "materialCode": "CRM0675",
    //                 "materialName": "Amine bottom Amix 1000 (CRM0675)",
    //                 "qty": 3000,
    //                 "reason": "Shortfall on 2025-01-21 = 974.9572, safety=3000.0, lead_time=5, reorder qty=3000.0"
    //             },
    //             {
    //                 "materialCode": "SRM0907",
    //                 "materialName": "C-IX Garasol 110 (SRM0907)",
    //                 "qty": 3784.7965,
    //                 "reason": "Shortfall on 2025-01-21 = -3784.7965, safety=0, lead_time=5, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "ORM0045",
    //                 "materialName": "Lauric Acid (ORM0045)",
    //                 "qty": 3000,
    //                 "reason": "Shortfall on 2025-01-21 = 134.5746, safety=3000.0, lead_time=5, reorder qty=3000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0460",
    //                 "materialName": "Light Magnesium Oxide(Lt. MGO) (CRM0460)",
    //                 "qty": 50,
    //                 "reason": "Shortfall on 2025-01-21 = 26.1536, safety=50.0, lead_time=5, reorder qty=50.0"
    //             },
    //             {
    //                 "materialCode": "CRM0110",
    //                 "materialName": "Maleic Anhydride (CRM0110)",
    //                 "qty": 6850.5048,
    //                 "reason": "Shortfall on 2025-01-21 = -6350.504800000001, safety=500.0, lead_time=5, reorder qty=500.0"
    //             },
    //             {
    //                 "materialCode": "SRM0905",
    //                 "materialName": "Mix Xylene (SRM0905)",
    //                 "qty": 36511.7791,
    //                 "reason": "Shortfall on 2025-01-21 = -31511.7791, safety=5000.0, lead_time=5, reorder qty=5000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0140",
    //                 "materialName": "OXALIC  ACID (CRM0140)",
    //                 "qty": 20,
    //                 "reason": "Shortfall on 2025-01-21 = 18.2995, safety=20.0, lead_time=5, reorder qty=20.0"
    //             },
    //             {
    //                 "materialCode": "CRM0315",
    //                 "materialName": "PERA TERTIARY BUTYL PHENOL(PTBP) (CRM0315)",
    //                 "qty": 10000,
    //                 "reason": "Shortfall on 2025-01-21 = 115.4356, safety=2500.0, lead_time=5, reorder qty=10000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0105",
    //                 "materialName": "Phthalic Anhydride (CRM0105)",
    //                 "qty": 193026.8905,
    //                 "reason": "Shortfall on 2025-01-21 = -187026.8905, safety=6000.0, lead_time=5, reorder qty=9000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0103(UV)",
    //                 "materialName": "Hypophosphrous Acid (CRM0103)",
    //                 "qty": 0.3821,
    //                 "reason": "Shortfall on 2025-01-21 = -0.3821, safety=0, lead_time=5, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "CRM0588(UV)",
    //                 "materialName": "SV CAT 102 (Butyltin tris {2-ethylhexoate}) (CRM0588)",
    //                 "qty": 0.718,
    //                 "reason": "Shortfall on 2025-01-21 = -0.718, safety=0, lead_time=5, reorder qty=0"
    //             }
    //         ],
    //         "reorders_arrived": [],
    //         "production_completed": []
    //     },
    //     {
    //         "date": "2025-01-17",
    //         "reorders_placed": [
    //             {
    //                 "materialCode": "CRM0110",
    //                 "materialName": "Maleic Anhydride (CRM0110)",
    //                 "qty": 500,
    //                 "reason": "Shortfall on 2025-01-22 = 499.9999999999991, safety=500.0, lead_time=5, reorder qty=500.0"
    //             }
    //         ],
    //         "reorders_arrived": [],
    //         "production_completed": []
    //     },
    //     {
    //         "date": "2025-01-19",
    //         "reorders_placed": [
    //             {
    //                 "materialCode": "CRM0133",
    //                 "materialName": "CRM0133 (Sulphuric Acid Technical 50 %)",
    //                 "qty": 500,
    //                 "reason": "Shortfall on 2025-01-21 = 195.0, safety=500.0, lead_time=2, reorder qty=500.0"
    //             },
    //             {
    //                 "materialCode": "CRM0707(UV)",
    //                 "materialName": "Sodium Hydroxide (Caustic Lye) 48 % Solution (CRM0707)",
    //                 "qty": 410,
    //                 "reason": "Shortfall on 2025-01-21 = -410.0, safety=0, lead_time=2, reorder qty=0"
    //             }
    //         ],
    //         "reorders_arrived": [],
    //         "production_completed": []
    //     },
    //     {
    //         "date": "2025-01-21",
    //         "reorders_placed": [
    //             {
    //                 "materialCode": "FAT0061",
    //                 "materialName": "Soya fatty acid Shankar Soya /Sap Oleo (FAT0061)",
    //                 "qty": 300250.459,
    //                 "reason": "Shortfall on 2025-01-21 = -295250.459, safety=5000.0, lead_time=0, reorder qty=5000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0705",
    //                 "materialName": "CRM0705 (Caustic Soda Flakes)",
    //                 "qty": 500,
    //                 "reason": "Shortfall on 2025-01-21 = 259.3967, safety=300.0, lead_time=0, reorder qty=500.0"
    //             },
    //             {
    //                 "materialCode": "ORM2009",
    //                 "materialName": null,
    //                 "qty": 5000,
    //                 "reason": "Shortfall on 2025-01-21 = 265.7758, safety=5000.0, lead_time=0, reorder qty=5000.0"
    //             },
    //             {
    //                 "materialCode": "PUV0904(UV)",
    //                 "materialName": "PUV0904 (P.T.S.A. (Para Toulene Sulphonic Acid))",
    //                 "qty": 0.333,
    //                 "reason": "Shortfall on 2025-01-21 = -0.333, safety=0, lead_time=0, reorder qty=0"
    //             }
    //         ],
    //         "reorders_arrived": [
    //             {
    //                 "materialCode": "CRM0335",
    //                 "qty": 25,
    //                 "reason": "Shortfall on 2025-01-21 = 9.2274, safety=10.0, lead_time=5, reorder qty=25.0"
    //             },
    //             {
    //                 "materialCode": "CRM0330",
    //                 "qty": 200,
    //                 "reason": "Shortfall on 2025-01-21 = 0.0003, safety=200.0, lead_time=5, reorder qty=200.0"
    //             },
    //             {
    //                 "materialCode": "CRM0675",
    //                 "qty": 3000,
    //                 "reason": "Shortfall on 2025-01-21 = 974.9572, safety=3000.0, lead_time=5, reorder qty=3000.0"
    //             },
    //             {
    //                 "materialCode": "SRM0907",
    //                 "qty": 3784.7965,
    //                 "reason": "Shortfall on 2025-01-21 = -3784.7965, safety=0, lead_time=5, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "CRM0133",
    //                 "qty": 500,
    //                 "reason": "Shortfall on 2025-01-21 = 195.0, safety=500.0, lead_time=2, reorder qty=500.0"
    //             },
    //             {
    //                 "materialCode": "CRM0203",
    //                 "qty": 110588.9151,
    //                 "reason": "Shortfall on 2025-01-21 = -110588.9151, safety=0, lead_time=7, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "ORM0045",
    //                 "qty": 3000,
    //                 "reason": "Shortfall on 2025-01-21 = 134.5746, safety=3000.0, lead_time=5, reorder qty=3000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0460",
    //                 "qty": 50,
    //                 "reason": "Shortfall on 2025-01-21 = 26.1536, safety=50.0, lead_time=5, reorder qty=50.0"
    //             },
    //             {
    //                 "materialCode": "CRM0110",
    //                 "qty": 6850.5048,
    //                 "reason": "Shortfall on 2025-01-21 = -6350.504800000001, safety=500.0, lead_time=5, reorder qty=500.0"
    //             },
    //             {
    //                 "materialCode": "SRM0905",
    //                 "qty": 36511.7791,
    //                 "reason": "Shortfall on 2025-01-21 = -31511.7791, safety=5000.0, lead_time=5, reorder qty=5000.0"
    //             },
    //             {
    //                 "materialCode": "SRM0901",
    //                 "qty": 46161.9417,
    //                 "reason": "Shortfall on 2025-01-21 = -26161.9417, safety=20000.0, lead_time=7, reorder qty=5000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0140",
    //                 "qty": 20,
    //                 "reason": "Shortfall on 2025-01-21 = 18.2995, safety=20.0, lead_time=5, reorder qty=20.0"
    //             },
    //             {
    //                 "materialCode": "CRM0315",
    //                 "qty": 10000,
    //                 "reason": "Shortfall on 2025-01-21 = 115.4356, safety=2500.0, lead_time=5, reorder qty=10000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0105",
    //                 "qty": 193026.8905,
    //                 "reason": "Shortfall on 2025-01-21 = -187026.8905, safety=6000.0, lead_time=5, reorder qty=9000.0"
    //             },
    //             {
    //                 "materialCode": "PUV0602",
    //                 "qty": 1,
    //                 "reason": "Shortfall on 2025-01-21 = -1.0, safety=0, lead_time=7, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "FAT0061",
    //                 "qty": 300250.459,
    //                 "reason": "Shortfall on 2025-01-21 = -295250.459, safety=5000.0, lead_time=0, reorder qty=5000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0713",
    //                 "qty": 440,
    //                 "reason": "Shortfall on 2025-01-21 = 280.87790000000007, safety=500.0, lead_time=10, reorder qty=440.0"
    //             },
    //             {
    //                 "materialCode": "CRM0705",
    //                 "qty": 500,
    //                 "reason": "Shortfall on 2025-01-21 = 259.3967, safety=300.0, lead_time=0, reorder qty=500.0"
    //             },
    //             {
    //                 "materialCode": "ORM2009",
    //                 "qty": 5000,
    //                 "reason": "Shortfall on 2025-01-21 = 265.7758, safety=5000.0, lead_time=0, reorder qty=5000.0"
    //             },
    //             {
    //                 "materialCode": "CRM0707(UV)",
    //                 "qty": 410,
    //                 "reason": "Shortfall on 2025-01-21 = -410.0, safety=0, lead_time=2, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "CRM0103(UV)",
    //                 "qty": 0.3821,
    //                 "reason": "Shortfall on 2025-01-21 = -0.3821, safety=0, lead_time=5, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "PUV0904(UV)",
    //                 "qty": 0.333,
    //                 "reason": "Shortfall on 2025-01-21 = -0.333, safety=0, lead_time=0, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "CRM0588(UV)",
    //                 "qty": 0.718,
    //                 "reason": "Shortfall on 2025-01-21 = -0.718, safety=0, lead_time=5, reorder qty=0"
    //             },
    //             {
    //                 "materialCode": "SRM0990",
    //                 "qty": 210874.3336,
    //                 "reason": "Shortfall on 2025-01-21 = -210694.3336, safety=180.0, lead_time=7, reorder qty=180.0"
    //             }
    //         ],
    //         "production_completed": []
    //     }
    // ]
    response.reorders.forEach(reorder => {
        let row = frm.add_child("purchase_actions");
        row.date = reorder.date;
        reorder.reorders_placed.forEach(item => {
            let child = frm.add_child("purchase_actions");
            child.date = reorder.date;
            child.material_code = item.materialCode;
            child.quantity = item.qty;
            child.reason = item.reason;
        });
    });
   
    frm.refresh_field("purchase_actions");
}