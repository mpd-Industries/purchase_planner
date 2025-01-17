frappe.ui.form.on("Production Plan", {
    onload: function (frm) {
        console.log("Production Plan loaded");
        toggle_batches_table(frm);

        if (!frm.doc.route) {
            frm.set_value("route", generate_route());
        }
        if (!frm.doc.published) {
            frm.set_value("published", 1);
        }

        update_published_webpage(frm);
    },

    stock_inventory: function (frm) {
        console.log("Stock Inventory changed:", frm.doc.stock_inventory);
        toggle_batches_table(frm);

        if (frm.doc.stock_inventory) {
            fetch_previous_batches(frm); // Fetch and populate previous batches
        }
    },

    before_save: function (frm) {
        if (!frm.doc.route) {
            frm.set_value("route", generate_route());
        }
        if (!frm.doc.published) {
            frm.set_value("published", 1);
        }
    },

    after_save: function (frm) {
        update_published_webpage(frm);
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

// Function to update the webpage display
function update_published_webpage(frm) {
    if (frm.doc.published) {
        const baseUrl = frappe.urllib.get_base_url();
        const route = frm.doc.route;
        const webpageUrl = `${baseUrl}/${route}`;
        frm.set_df_property(
            "overall_materials_requirement",
            "options",
            `<a href="${webpageUrl}" target="_blank">${webpageUrl}</a>`
        );
    }
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
                populate_tables(frm, response.message.material_requirements, response.message.reorders);
            }
        }
    });
}

// Function to populate the child tables
function populate_tables(frm, materialRequirements, reorders) {
    // Populate Material Requirement Per Day Table
    frm.clear_table("material_requirement_per_day");
    materialRequirements.forEach(day => {
        Object.entries(day.usage || {}).forEach(([material, qty]) => {
            let row = frm.add_child("material_requirement_per_day");
            row.date = day.date;
            row.material_code = material;
            row.quantity_used = qty;
        });
    });
    frm.refresh_field("material_requirement_per_day");

    // Populate Overall Materials Requirement Table
    frm.clear_table("overall_materials_requirement");
    const overallTotals = {};
    materialRequirements.forEach(day => {
        Object.entries(day.usage || {}).forEach(([material, qty]) => {
            overallTotals[material] = (overallTotals[material] || 0) + qty;
        });
    });
    Object.entries(overallTotals).forEach(([material, totalQty]) => {
        let row = frm.add_child("overall_materials_requirement");
        row.material_code = material;
        row.total_quantity = totalQty;
        row.total_reorder_quantity = reorders.reduce((total, reorder) => {
            if (reorder.reorders_placed[material]) {
                return total + reorder.reorders_placed[material].qty;
            }
            return total;
        }, 0);
    });
    frm.refresh_field("overall_materials_requirement");

    // Populate Purchase Actions Table
    frm.clear_table("purchase_actions");
    reorders.forEach(reorder => {
        Object.entries(reorder.reorders_placed || {}).forEach(([material, details]) => {
            let row = frm.add_child("purchase_actions");
            row.date = reorder.date;
            row.material_code = material;
            row.quantity = details.qty;
            row.reason = details.reason;
        });
    });
    frm.refresh_field("purchase_actions");
}
