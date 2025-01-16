frappe.ui.form.on('Day Stock', {
    refresh: function (frm) {
        frm.add_custom_button(__('Upload Excel'), function () {
            // Trigger the file upload dialog
            new frappe.ui.FileUploader({
                folder: 'Home',
                on_success: function (file) {
                    frappe.call({
                        method: 'purchase_planner.purchase_planner.doctype.day_stock.day_stock.upload_stock_excel',
                        args: {
                            file_url: file.file_url
                        },
                        callback: function (r) {
                            console.log(r);
                            if (!r.exc) {
                                console.log(r.message);
                                const { updated_table, error_list } = r.message;
                                
                                 // Log the response to verify the data

                                // Refresh the Stock Log child table
                                if (updated_table.length > 0) {
                                    frm.clear_table("table_fpim"); // Clear existing table data
                                    updated_table.forEach(item => {
                                        frm.add_child("table_fpim", item); // Add updated rows
                                    });
                                    frm.refresh_field("table_fpim"); // Refresh the table field
                                }

                                // Show success or error message
                                if (error_list.length > 0) {
                                    frappe.msgprint(
                                        `Stock log updated with errors. Missing materials: ${error_list.join(", ")}`,
                                        "Partial Success",
                                        "orange"
                                    );
                                } else {
                                    frappe.msgprint(__('Stock log updated successfully.'));
                                }
                            }
                        }
                    });
                }
            });
        });
    }
});
