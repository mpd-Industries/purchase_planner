Here is the updated requirements document based on the provided doctypes:

---

# Requirements Document: Production Planning and Stock Management System

## Purpose
The purpose of this system is to manage production plans efficiently, ensuring that raw materials and reactors are optimally utilised, stock levels are maintained, and reorder processes are automated to meet production demands. The system will allow for weekly production planning with validations for stock and reactor availability.

---

## Key Features

### 1. Raw Material Management
**Fields:**
- **Material Code:** Unique identifier for the raw material (required, unique).
- **Material Name:** Descriptive name of the material.
- **Lead Time (days):** Time required to procure the material (non-negative, required).
- **Safety Stock (Kg):** Minimum stock level to maintain (default to 0, required).
- **Reorder Quantity (Kg):** Minimum quantity to reorder (required).
- **Unit of Measure:** Units in which the material is measured.
- **Cost:** Cost per unit of material (not explicitly provided in the doctype but can be considered a future enhancement).

**Validations:**
- Stock levels cannot fall below the safety stock level without triggering a reorder.

**Functionality:**
- Track stock levels for raw materials.
- Automatically generate reorder recommendations when stock levels fall below the safety stock threshold.

---

### 2. Product Management
**Fields:**
- **Product Code:** Unique identifier for the product.
- **Description:** Detailed description of the product.
- **Batch Size (Kg):** Standard size for a production batch.

---

### 3. Formulation Management
**Fields:**
- **Formulation ID:** Unique identifier for the formulation (required).
- **Material ID:** Link to the raw material used in the formulation.
- **Batch Size (Kg):** Default batch size for the formulation (required).
- **Formulation Table:** List of raw materials and their quantities required for the formulation.

**Functionality:**
- Scale raw material quantities based on the production batch size.
- Validate the existence of raw material codes during formulation creation.

---

### 4. Reactor Management
**Fields:**
- **Reactor Name:** Unique identifier for the reactor (required).
- **Capacity (Kg):** Maximum capacity of the reactor (required, non-negative).


---

### 5. Production Plan Management
**Fields:**
- **Reactor:** Assigned reactor for the batch.
- **Product:** Product being produced.
- **Formulation:** Formulation used for the batch.
- **Batch Size (Kg):** Quantity of product to be produced.
- **Planned Date:** Date for the batch production.

**Validations:**
- Check reactor availability for the given date.
- Check raw material availability for the required batch size.

---

### 6. Stock Log Management
**Fields:**
- **Material Code:** Link to the raw material being tracked.
- **Stock (Kg):** Current stock level of the material.
- **Date and Time:** Timestamp for stock adjustments.

**Functionality:**
- Maintain a log of stock adjustments due to production or procurement.

---

### 7. Reorder Automation
**Process:**
- Calculate the total raw material requirement for the production plan.
- Compare with current stock levels.
- Generate reorder recommendations based on:
  - Lead time.
  - Minimum reorder quantities.
  - Safety stock levels.

---

### 8. Reporting and Insights
**Reports:**
- Stock levels and reorder recommendations.
- Weekly production summaries.
- Raw material consumption trends.

**Functionality:**
- Provide insights into reactor utilisation and raw material usage.

---

## System Workflow

1. **Data Setup:**
   - Add raw materials, products, formulations, and reactors.

2. **Create Production Plan:**
   - Add multiple batches for the week.
   - Validate stock and reactor availability.

3. **Execute Plan:**
   - Update stock logs based on material consumption.
   - Mark reactors as busy or available.

4. **Generate Reports:**
   - Review stock levels, production summaries, and reactor utilisation.

---

## Technological Stack

- **Framework:** Frappe
- **Database:** MariaDB (default for Frappe)
- **Backend Logic:** Python (Custom Scripts in Frappe)
- **Frontend:** Frappe UI

---

## Future Enhancements

- Integration with procurement systems for automatic purchase order creation.
- Advanced analytics for forecasting raw material requirements.
- Mobile application for real-time stock and production tracking.

---