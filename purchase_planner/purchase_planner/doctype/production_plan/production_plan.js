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
    download_production_plan: function (frm) {
        calculate_material_requirements(frm).then((message) => {
            if (message) {
                generateCSV(message);
            }
        });
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
                    frm.refresh_field("batches");

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
                response.message[0].forEach(batch => {
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
    calculate_material_requirements(frm).then((message) => {
        console.log("Material requirements calculated:", message);
        if (message) {
            populate_tables(frm, message);
        }
    });
}

function calculate_material_requirements(frm) {
    if (!frm.doc.stock_inventory || !frm.doc.batches) {
        console.log("No stock inventory or batches to send to the server.");
        return;
    }
    return frappe.call({
        method: "purchase_planner.purchase_planner.doctype.production_plan.production_plan.calculate_material_requirements",
        args: {
            stock_inventory: frm.doc.stock_inventory,
            batches: frm.doc.batches
        }
    }).then(response => {
        console.log("Material requirements calculated:", response.message);
        return response.message;
    });
}

// Function to populate the child tables
function populate_tables(frm, response) {
    // Populate Material Requirement Per Day Table
    frm.clear_table("material_requirement_per_day");
    
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
}

function generateCSV(data) {
    let csvContent = "data:text/csv;charset=utf-8,";

    // Extract overall material info (Safety Stock, Current Stock)
    let materialsMap = new Map();

    data.overall_material_requirements.forEach(material => {
        materialsMap.set(material.materialCode, {
            materialName: material.materialName || "N/A",
            safetyStock: material.safetyStock || 0,
            currentStock: material.currentStock || 0,
            totalUsed: 0, // Will be filled later
            shortfall: 0, // Calculated later
            usage: {} // Stores usage per date
        });
    });

    // Extract unique dates for columns
    let uniqueDates = new Set();
    data.material_requirements.forEach(entry => uniqueDates.add(entry.date));
    uniqueDates = Array.from(uniqueDates).sort(); // Sort dates for correct order

    // Populate usage from material_requirements
    data.material_requirements.forEach(entry => {
        entry.materials.forEach(material => {
            if (!materialsMap.has(material.materialCode)) {
                materialsMap.set(material.materialCode, {
                    materialName: material.materialName || "N/A",
                    safetyStock: 0,
                    currentStock: 0,
                    totalUsed: 0,
                    shortfall: 0,
                    usage: {}
                });
            }
            materialsMap.get(material.materialCode).usage[entry.date] = {
                usage: material.usage,
                prevUsage: 0, // Will be filled later
                delta: 0
            };
        });
    });

    // Populate previous usage from prev_material_requirements
    data.prev_material_requirements.forEach(prevMaterial => {
        let material = materialsMap.get(prevMaterial.material_code);
        if (material) {
            if (!material.usage[prevMaterial.date]) {
                material.usage[prevMaterial.date] = { usage: 0, prevUsage: 0, delta: 0 };
            }
            material.usage[prevMaterial.date].prevUsage = prevMaterial.quantity_used;
        }
    });

    // Compute deltas and total used
    materialsMap.forEach(material => {
        let prevUsage = 0;
        uniqueDates.forEach(date => {
            if (!material.usage[date]) {
                material.usage[date] = { usage: 0, prevUsage: prevUsage, delta: -prevUsage };
            } else {
                material.usage[date].delta = material.usage[date].usage - material.usage[date].prevUsage;
            }
            prevUsage = material.usage[date].usage;
            material.totalUsed += material.usage[date].usage;
        });

        // Compute Shortfall
        material.shortfall = Math.max(0, material.totalUsed - material.currentStock);
    });

    // Create header row
    let header = ["Material Code", "Material Name", "Safety Stock", "Current Stock", "Overall Requirement", "Shortfall"];
    uniqueDates.forEach(date => {
        header.push(`${date} Usage`, `${date} Prev Usage`, `${date} Delta`);
    });
    csvContent += header.join(",") + "\n";

    // Populate rows with material data
    materialsMap.forEach((material, code) => {
        let row = [
            code,
            material.materialName,
            material.safetyStock,
            material.currentStock,
            material.totalUsed,
            material.shortfall
        ];

        // Add usage, previous usage, and delta for each date
        uniqueDates.forEach(date => {
            let usageData = material.usage[date] || { usage: 0, prevUsage: 0, delta: 0 };
            row.push(usageData.usage, usageData.prevUsage, usageData.delta);
        });

        csvContent += row.join(",") + "\n";
    });

    // Create a download link and trigger it
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    // todays date in string format
    const today = new Date().toISOString();
    // use that for the filename
    link.setAttribute("download", `Material_Usage_Report_${today}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
