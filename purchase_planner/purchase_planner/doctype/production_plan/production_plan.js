frappe.ui.form.on("Production Plan", {
    // Triggered when the form is loaded
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

    // Triggered when the stock_inventory field changes
    stock_inventory: function (frm) {
        console.log("Stock Inventory changed:", frm.doc.stock_inventory);
        toggle_batches_table(frm);

        if (frm.doc.stock_inventory) {
            fetch_previous_batches(frm); // Fetch and populate previous batches
        }
    },

    // Triggered before saving the document
    before_save: function (frm) {
        if (!frm.doc.route) {
            frm.set_value("route", generate_route());
        }
        if (!frm.doc.published) {
            frm.set_value("published", 1);
        }
    },

    // Triggered after saving the document
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
    } else {
        frm.set_df_property(
            "overall_materials_requirement",
            "options",
            "<p>The production plan is not published yet.</p>"
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
                    // Map fields from response to child table
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
        frappe.msgprint("A row has been added to the batches table.");
        debounce_send_batches_to_server(frm);
    },

    batches_remove: function (frm) {
        console.log("Row removed from batches table:", frm.doc.batches);
        frappe.msgprint("A row has been removed from the batches table.");
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
            if (response.message) {
                // Update fields with returned data
                frm.set_value("material_requirement_per_day", response.message.material_requirement_per_day);
                frm.set_value("overall_materials_requirement", response.message.overall_materials_requirement);
            }
        }
    });
}
