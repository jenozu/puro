"""Puro v0.1 — review-only SAP export shipping workstation.

NO Purolator shipment creation is available in this increment.
"""
from __future__ import annotations

import csv
from io import BytesIO, StringIO

import pandas as pd
import streamlit as st

from puro.config import AppConfig, ConfigurationError
from puro.auditlog import configure_logging, log_event
from puro.address import recipient_snapshot, AddressProblem
from puro.orders import ImportProblem, parse_export, review_errors
from puro.store import Store
from puro.journal import ShipmentJournal
from puro.batch import BatchRunner, history as shipment_history, shipment_history_csv, label_zip
from puro.carrier import PurolatorSOAP, CarrierConfigurationError
from puro.shipping import ShippingCoordinator
from puro.documents import LabelService

st.set_page_config(page_title="Puro Shipping Workstation", page_icon="📦", layout="wide")

try:
    config = AppConfig.from_environment()
except ConfigurationError as exc:
    st.error(str(exc))
    st.stop()

configure_logging()
journal = ShipmentJournal(config.data_dir / "puro.sqlite3")
store = journal.store
log_event("app_initialized", config, environment=config.environment, status="review_only")

st.title("Puro | Shipping Workstation")
st.caption("SAP export shipping workstation — carrier submission is disabled by default.")

environment = config.environment
if environment == "production":
    st.warning("PRODUCTION environment selected. Shipment creation remains locked until explicitly authorized.")
elif not config.carrier_enabled:
    st.info("Offline preparation mode: Purolator calls are disabled. Reviewing an order does NOT create a shipment.")

view = st.sidebar.radio(
    "Workspace", ["Import SAP export", "Orders", "Review a shipment", "History & status"],
)
st.sidebar.caption("v0.1 development increment · Local SQLite · No SAP login needed")


def frame() -> pd.DataFrame:
    items = store.list_orders()
    return pd.DataFrame([
        {
            "DocNum": item["doc_num"],
            "DocEntry": item["doc_entry"],
            "Customer": item["customer_name"],
            "Ship To": ", ".join([
                str(item["data"].get(k, "")).strip()
                for k in ("street_no", "street", "city", "province")
                if item["data"].get(k)
            ]),
            "Status": item["status"],
        }
        for item in items
    ])


if view == "Import SAP export":
    st.subheader("Import an Excel or CSV export")
    st.write(
        "Upload an XLSX workbook with an **Orders** worksheet, or a CSV with matching "
        "headers. The new export uses only **ORDR.Address2 / FullShipToAddress** for the original destination. "
        "Billing addresses are never used as a substitute."
    )
    template = BytesIO()
    with pd.ExcelWriter(template, engine="openpyxl") as writer:
        pd.DataFrame(columns=[
            "DocEntry", "DocNum", "CardCode", "CustomerName", "CustomerPO",
            "FullShipToAddress",
        ]).to_excel(writer, sheet_name="Orders", index=False)
        pd.DataFrame(columns=[
            "DocEntry", "LineNum", "ItemCode", "LineShipToCode"
        ]).to_excel(writer, sheet_name="OrderLines", index=False)
    st.download_button(
        "Download empty SAP export template",
        template.getvalue(), file_name="puro_sap_export_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    uploaded = st.file_uploader("SAP orders", type=["xlsx", "csv"])
    if uploaded:
        try:
            parsed = parse_export(uploaded.getvalue(), uploaded.name)
            st.write(f"Found **{len(parsed.orders)}** orders. "
                     f"File checksum: `{parsed.sha256[:12]}`.")
            if parsed.issues:
                st.warning("Import notes: " + " | ".join(parsed.issues[:12]))
            if parsed.orders:
                preview = pd.DataFrame([
                    {
                        "Order": r["doc_num"],
                        "Customer": r["customer_name"],
                        "Ship To City": r["city"],
                        "Ship To Province": r["province"],
                        "Needs review": "; ".join(r["import_warnings"])
                             or ("Line Ship To conflict" if r["line_conflict"] else ""),
                    }
                    for r in parsed.orders
                ])
                st.dataframe(preview, use_container_width=True, hide_index=True)
                if st.button("Import these orders", type="primary"):
                    added, existing = store.import_orders(parsed, uploaded.name)
                    st.success(
                        f"Imported {added} new order(s). Preserved {existing} existing "
                        "order(s) without overwriting edits or shipping history."
                    )
            else:
                st.warning("No open/non-cancelled orders found in this export.")
        except ImportProblem as exc:
            st.error(str(exc))
    st.caption("Use the format described in docs/SAP_EXPORT.md. This app does not connect to SAP.")

elif view == "Orders":
    st.subheader("Orders in the local database")
    query = st.text_input("Search DocNum, DocEntry, CardCode or customer")
    orders = store.list_orders(query)
    st.write(f"{len(orders)} matching order(s)")
    st.dataframe(
        pd.DataFrame([
            {
                "Order #": o["doc_num"],
                "DocEntry": o["doc_entry"],
                "Customer": o["customer_name"],
                "Recipient": o["data"].get("ship_to_name", ""),
                "City": o["data"].get("city", ""),
                "Status": o["status"],
            }
            for o in orders
        ]),
        hide_index=True, use_container_width=True,
    )
    eligible = [o for o in orders if o["status"] in {"needs_review", "ready"}]
    staged = st.session_state.get("review_batch", [])
    selected_entries = st.multiselect(
        "Select orders for a review batch (no carrier calls)",
        options=[o["doc_entry"] for o in eligible],
        default=[o["doc_entry"] for o in eligible if o["doc_entry"] in staged],
        format_func=lambda v: next(
            (f"#{o['doc_num']} — {o['customer_name']}" for o in eligible
             if o["doc_entry"] == v), v
        ),
    )
    if st.button("Stage selected orders for review"):
        st.session_state["review_batch"] = selected_entries
        st.success(f"{len(selected_entries)} order(s) staged for review.")
    st.caption("Orders marked Ready have been locally reviewed; none have been sent to Purolator.")

elif view == "Review a shipment":
    st.subheader("Verify destination and package details")
    rows = store.list_orders()
    if not rows:
        st.info("Import a SAP export first.")
    else:
        choices = {f"#{o['doc_num']} — {o['customer_name']} (DocEntry {o['doc_entry']})":
                   o["doc_entry"] for o in rows}
        staged = set(st.session_state.get("review_batch", []))
        if staged:
            choices = {label: doc_entry for label, doc_entry in choices.items()
                       if doc_entry in staged}
            st.caption(f"Reviewing {len(choices)} staged orders. Clear the batch in Orders to review all.")
        selected = st.selectbox("Select sales order", list(choices)) if choices else None
        if not choices:
            st.warning("No staged orders are available for review. Re-select them under Orders.")
            st.stop()
        entry = choices[selected]
        saved = store.get_order(entry)
        assert saved is not None
        order = saved["data"]
        st.write(f"**Status:** {saved['status']}")
        st.warning(
            "Confirm the actual Ship To from SAP before marking Ready. "
            "The customer billing address is NOT a valid automatic fallback."
        )
        source = order.get("source_address")
        if source:
            st.markdown("#### Original SAP Ship To (read-only)")
            st.json(source)
            if order.get("full_ship_to_address"):
                st.warning(
                    "Fill the fields below using only this original address. "
                    "For Cash Sale, use the recipient name before the street number. "
                    "Check all values manually: no inferred address is approved automatically."
                )

        with st.form(f"review_{entry}"):
            st.markdown("#### Recipient / Ship To")
            name = st.text_input("Ship To recipient name", value=order.get("ship_to_name", ""),
                                 key=f"{entry}_name")
            c1, c2 = st.columns([1, 3])
            with c1:
                street_no = st.text_input("Street number", value=order.get("street_no", ""),
                                          key=f"{entry}_streetno")
            with c2:
                street = st.text_input("Street name / address", value=order.get("street", ""),
                                       key=f"{entry}_street")
            building = st.text_input("Building / unit", value=order.get("building", ""),
                                     key=f"{entry}_building")
            address2 = st.text_input("Address line 2", value=order.get("address2", ""),
                                     key=f"{entry}_address2")
            address3 = st.text_input("Address line 3", value=order.get("address3", ""),
                                     key=f"{entry}_address3")
            c1, c2 = st.columns(2)
            with c1:
                city = st.text_input("City", value=order.get("city", ""), key=f"{entry}_city")
                postal_code = st.text_input("Postal code", value=order.get("postal_code", ""),
                                             key=f"{entry}_postal")
            with c2:
                province = st.text_input("Province (e.g. ON)", value=order.get("province", ""),
                                          key=f"{entry}_province")
                country = st.text_input("Country (CA)", value=order.get("country", ""),
                                         key=f"{entry}_country")
            phone = st.text_input("Phone", value=order.get("phone", ""), key=f"{entry}_phone")

            st.markdown("#### Physically packed carton")
            st.caption("The initial review increment supports one parcel. Use measured "
                       "package dimensions, not item-master dimensions.")
            p1, p2, p3, p4 = st.columns(4)
            with p1:
                weight = st.number_input("Weight (lb)", min_value=0.0,
                                         value=float(order.get("weight_lb", 0)), step=0.5,
                                         key=f"{entry}_weight")
            with p2:
                length = st.number_input("Length (in)", min_value=0.0,
                                         value=float(order.get("length_in", 0)), step=1.0,
                                         key=f"{entry}_length")
            with p3:
                width = st.number_input("Width (in)", min_value=0.0,
                                        value=float(order.get("width_in", 0)), step=1.0,
                                        key=f"{entry}_width")
            with p4:
                height = st.number_input("Height (in)", min_value=0.0,
                                         value=float(order.get("height_in", 0)), step=1.0,
                                         key=f"{entry}_height")
            service = st.selectbox(
                "Requested service (NOT yet verified with Purolator)",
                ["PurolatorGround", "PurolatorExpress"],
                index=0 if order.get("service") != "PurolatorExpress" else 1,
                key=f"{entry}_service",
            )
            payer = st.radio(
                "Billing", ["Sender", "Receiver"],
                index=0 if order.get("payer") != "Receiver" else 1,
                horizontal=True, key=f"{entry}_payer",
            )
            receiver_account = st.text_input(
                "Receiver Purolator account (required if Receiver pays)",
                value=order.get("receiver_account", ""), key=f"{entry}_account",
            )
            saved_clicked = st.form_submit_button("Save draft (requires reconfirmation)")
            if saved_clicked:
                try:
                    store.save_draft(entry, {
                        "ship_to_name": name, "street_no": street_no, "street": street,
                        "building": building, "address2": address2, "address3": address3,
                        "city": city, "province": province.upper(), "postal_code": postal_code,
                        "country": country.upper(), "phone": phone,
                        "weight_lb": weight, "length_in": length, "width_in": width,
                        "height_in": height, "pieces": 1, "service": service, "payer": payer,
                        "receiver_account": receiver_account,
                    })
                    st.success("Draft saved. Review and confirm its details below.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        latest = store.get_order(entry)
        assert latest is not None
        issues = review_errors(latest["data"])
        if issues:
            for issue in issues:
                st.error(issue)
        else:
            st.success("Local preflight passed. Review destination and shipping details.")
            recipient = recipient_snapshot(latest["data"])
            st.write("**Final receiver snapshot — shipping fields only:**")
            st.json(recipient)
            st.write("**Requested package / service:**")
            st.json({
                "order": latest["doc_num"],
                "recipient": latest["data"]["ship_to_name"],
                "street_no": latest["data"]["street_no"],
                "street": latest["data"]["street"],
                "building": latest["data"]["building"],
                "address2": latest["data"]["address2"],
                "address3": latest["data"]["address3"],
                "city": latest["data"]["city"],
                "province": latest["data"]["province"],
                "postal": latest["data"]["postal_code"],
                "country": latest["data"]["country"],
                "service_request": latest["data"]["service"],
                "payer": latest["data"]["payer"],
                "weight_lb": latest["data"]["weight_lb"],
                "length_in": latest["data"]["length_in"],
                "width_in": latest["data"]["width_in"],
                "height_in": latest["data"]["height_in"],
            })
        pending = latest["data"].get("address_review_pending", False)
        if pending and not issues:
            verified = st.checkbox(
                "I verified the recipient name and all shipping address fields "
                "against the original SAP FullShipToAddress displayed above."
            )
            if st.button("Confirm original SAP address", disabled=not verified):
                try:
                    store.confirm_address_review(entry)
                    st.success("Address verified. Mark Ready separately.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if st.button(
            "Confirm review — mark Ready (does NOT ship)",
            disabled=bool(issues) or pending or latest["status"] not in {"ready", "needs_review"},
            type="primary",
        ):
            try:
                store.confirm_ready(entry)
                st.success("Order marked Ready. No carrier request was sent.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

elif view == "History & status":
    st.subheader("Local review status and exports")
    summary = store.summary()
    a, b, c = st.columns(3)
    a.metric("Total imported", sum(summary.values()))
    b.metric("Ready for future integration", summary.get("ready", 0))
    c.metric("Needs review", summary.get("needs_review", 0))
    batch = set(st.session_state.get("review_batch", []))
    if batch:
        st.markdown("#### Staged review batch")
        preview = []
        for o in store.list_orders():
            if o["doc_entry"] not in batch:
                continue
            problems = review_errors(o["data"])
            preview.append({
                "Order": o["doc_num"],
                "Ship To": f"{o['data'].get('street', '')}, {o['data'].get('city', '')}",
                "Review status": o["status"],
                "Local errors": "; ".join(problems) or "None",
            })
        st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
        ready_batch = [o["doc_entry"] for o in store.list_orders()
                       if o["doc_entry"] in batch and o["status"] == "ready"]
        if not config.carrier_enabled:
            st.warning("This batch is for review ONLY. Purolator shipment creation is disabled.")
            st.info("Carrier execution is OFF. Set up and authorize the local API environment before attempting to ship.")
        else:
            st.markdown("#### Carrier submission — explicit action only")
            selected_pickup = st.selectbox(
                "Purolator pickup arrangement", ["Select...", "DropOff", "PreScheduled"]
            )
            all_ready = len(ready_batch) == len(batch) and bool(batch)
            if not all_ready:
                st.warning("All staged orders must be locally confirmed Ready before carrier submission.")
            missing = config.missing_shipping_settings()
            if missing:
                st.error("Local shipping settings incomplete: " + ", ".join(missing))
            if environment == "production" and not config.production_authorized:
                st.error("Production is locked: authorization must first be recorded locally after controlled sign-off.")
            confirmed = st.checkbox(
                "I approve these specific carrier shipment requests "
                + ("as LIVE, potentially chargeable shipments." if environment == "production"
                   else "in the Purolator DEVELOPMENT environment.")
            )
            allowed = (
                all_ready and not missing and selected_pickup != "Select..."
                and confirmed and (environment != "production" or config.production_authorized)
            )
            if st.button("Create selected Purolator shipments", disabled=not allowed,
                         type="primary"):
                try:
                    carrier = PurolatorSOAP(config, config.wsdl_dir)
                    coordinator = ShippingCoordinator(config, journal, carrier)
                    labels = LabelService(config, journal, carrier)
                    with st.spinner("Processing reviewed shipments"):
                        results = BatchRunner(coordinator, labels).run(
                            ready_batch, pickup_type=selected_pickup,
                            production_approved=environment == "production" and confirmed
                                and config.production_authorized,
                        )
                    st.dataframe(pd.DataFrame([vars(x) for x in results]),
                                 hide_index=True, use_container_width=True)
                    st.warning("Verify every PIN and saved PDF before attaching labels.")
                except CarrierConfigurationError as exc:
                    st.error(str(exc))
    with st.expander("Recent review audit events"):
        events = store.audit_events(limit=50)
        st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
    shipped = shipment_history(journal)
    if shipped:
        st.markdown("#### Persistent Purolator shipment records")
        st.dataframe(pd.DataFrame([{
            "SAP Order": r["doc_num"], "PIN": r["shipment_pin"],
            "Status": r["status"], "Environment": r["environment"],
            "Created": r["created_at"],
        } for r in shipped]), hide_index=True, use_container_width=True)
        st.download_button(
            "Download shipment tracking CSV",
            shipment_history_csv(journal),
            "puro_shipments.csv", "text/csv",
        )
        completed = [r for r in shipped if r["status"] == "label_ready"]
        if completed:
            st.download_button(
                "Download saved label PDFs as ZIP (no carrier calls)",
                label_zip(journal),
                "puro_labels.zip", "application/zip",
            )
    ready = [o for o in store.list_orders() if o["status"] == "ready"]
    if ready:
        def csv_safe(value: object) -> str:
            text = str(value if value is not None else "")
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "DocEntry", "DocNum", "CardCode", "CustomerName", "ShipToName",
            "ShipToStreetNo", "ShipToStreet", "ShipToBuilding",
            "ShipToAddress2", "ShipToAddress3", "ShipToCity",
            "ShipToProvince", "ShipToPostalCode", "ShipToCountry",
            "WeightLB", "LengthIn", "WidthIn", "HeightIn",
            "ServiceRequest", "Payer",
        ])
        for item in ready:
            d = item["data"]
            writer.writerow([csv_safe(v) for v in [
                item["doc_entry"], item["doc_num"], d.get("card_code", ""),
                item["customer_name"], *(
                    d.get(x, "") for x in (
                        "ship_to_name", "street_no", "street", "building", "address2",
                        "address3", "city", "province", "postal_code", "country",
                        "weight_lb", "length_in", "width_in", "height_in", "service", "payer"
                    )
                )
            ]])
        st.download_button(
            "Download reviewed orders CSV (not carrier labels)",
            buffer.getvalue().encode("utf-8-sig"),
            "puro_reviewed_orders.csv", "text/csv",
        )
    else:
        st.info("No orders are marked Ready.")
    if not shipped:
        st.info("No carrier shipments have been created in this local database.")
