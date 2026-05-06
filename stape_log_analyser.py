import streamlit as st
import pandas as pd
import json
from collections import Counter

st.set_page_config(page_title="Stape Log Analyser", layout="wide")
st.title("Stape Log Analyser")

# ── helpers ────────────────────────────────────────────────────────────────

def parse_csv(uploaded_file) -> pd.DataFrame:
    return pd.read_csv(uploaded_file)


def extract_outgoing(body: str) -> dict:
    """Parse Request-Body for Meta CAPI / GA4 outgoing events."""
    try:
        d = json.loads(body)
        # Meta CAPI structure: {"data": [{"event_name": ..., "custom_data": {...}}]}
        if "data" in d and isinstance(d["data"], list):
            ev = d["data"][0]
            cd = ev.get("custom_data", {})
            ud = ev.get("user_data", {})
            return {
                "event_name":  ev.get("event_name", ""),
                "event_id":    ev.get("event_id", ""),
                "event_time":  ev.get("event_time", ""),
                "order_id":    cd.get("order_id", ""),
                "value":       cd.get("value", ""),
                "currency":    cd.get("currency", ""),
                "content_ids": cd.get("content_ids", ""),
                "action_source": ev.get("action_source", ""),
                "source_url":  ev.get("event_source_url", ""),
                "has_email":   bool(ud.get("em")),
                "has_phone":   bool(ud.get("ph")),
                "has_fbp":     bool(ud.get("fbp")),
                "has_fbc":     bool(ud.get("fbc")),
                "partner_agent": d.get("partner_agent", ""),
            }
        # GA4 / generic flat structure
        events = d.get("events", [])
        if events:
            ev = events[0]
            params = ev.get("params", {})
            return {
                "event_name":  ev.get("name", ""),
                "event_id":    params.get("event_id") or d.get("event_id", ""),
                "event_time":  "",
                "order_id":    params.get("transaction_id", ""),
                "value":       params.get("value", ""),
                "currency":    params.get("currency", ""),
                "content_ids": "",
                "action_source": "",
                "source_url":  d.get("event_source_url", ""),
                "has_email":   False,
                "has_phone":   False,
                "has_fbp":     False,
                "has_fbc":     False,
                "partner_agent": "",
            }
        return {}
    except Exception:
        return {}


def extract_incoming(body: str) -> dict:
    """Parse Response-Body for server responses."""
    try:
        d = json.loads(body)
        return {
            "events_received": d.get("events_received", ""),
            "fbtrace_id":      d.get("fbtrace_id", ""),
            "messages":        "; ".join(d.get("messages", [])) if isinstance(d.get("messages"), list) else str(d.get("messages", "")),
            "error":           d.get("error", {}).get("message", "") if isinstance(d.get("error"), dict) else "",
        }
    except Exception:
        return {"events_received": "", "fbtrace_id": "", "messages": body[:120] if body else "", "error": ""}


def flag_duplicates(df: pd.DataFrame, key_col: str) -> pd.DataFrame:
    if key_col not in df.columns:
        return df
    counts = df[key_col].value_counts()
    df["_dup_count"] = df[key_col].map(counts)
    df["is_duplicate"] = df["_dup_count"] > 1
    return df


def status_color(code):
    if code == 200:
        return "🟢"
    if 400 <= code < 500:
        return "🟡"
    if code >= 500:
        return "🔴"
    return "⚪"


# ── sidebar ────────────────────────────────────────────────────────────────

st.sidebar.header("Settings")
mode = st.sidebar.radio("Analyse mode", ["Outgoing requests", "Incoming responses", "Both (side by side)"])
uploaded = st.sidebar.file_uploader("Upload Stape CSV log", type=["csv"])

# ── main ───────────────────────────────────────────────────────────────────

if uploaded is None:
    st.info("Upload a CSV exported from the Stape request log to get started.")
    st.caption("Expected columns: Date, Trace Id, Platform, Event type, Status Code, Request-Body, Response-Body")
    st.stop()

raw = parse_csv(uploaded)

# Normalise column names (strip spaces)
raw.columns = [c.strip() for c in raw.columns]

st.sidebar.markdown("---")
st.sidebar.metric("Total rows", len(raw))

# ── filters (sidebar) ──────────────────────────────────────────────────────

if "Event type" in raw.columns:
    event_types = ["All"] + sorted(raw["Event type"].dropna().unique().tolist())
    sel_event = st.sidebar.selectbox("Filter by event type", event_types)
    if sel_event != "All":
        raw = raw[raw["Event type"] == sel_event]

if "Status Code" in raw.columns:
    status_codes = ["All"] + sorted(raw["Status Code"].dropna().astype(str).unique().tolist())
    sel_status = st.sidebar.selectbox("Filter by status code", status_codes)
    if sel_status != "All":
        raw = raw[raw["Status Code"].astype(str) == sel_status]

show_dups_only = st.sidebar.checkbox("Show duplicates only")

# ── parse bodies ───────────────────────────────────────────────────────────

out_parsed = raw["Request-Body"].fillna("{}").apply(extract_outgoing).apply(pd.Series)
in_parsed  = raw["Response-Body"].fillna("{}").apply(extract_incoming).apply(pd.Series)

base_cols = ["Date", "Trace Id", "Status Code", "Platform"] if all(c in raw.columns for c in ["Date", "Trace Id", "Status Code", "Platform"]) else raw.columns.tolist()
base = raw[base_cols].copy()
base["status_icon"] = base["Status Code"].apply(lambda x: status_color(int(x)) if str(x).isdigit() else "⚪")

# ── OUTGOING ───────────────────────────────────────────────────────────────

def show_outgoing():
    st.subheader("Outgoing requests (Request-Body)")

    df = pd.concat([base, out_parsed], axis=1)
    df = flag_duplicates(df, "event_id")

    if show_dups_only:
        df = df[df["is_duplicate"] == True]

    # summary metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Rows", len(df))
    c2.metric("Unique event_ids", df["event_id"].nunique() if "event_id" in df.columns else "—")
    c3.metric("Unique orders", df["order_id"].nunique() if "order_id" in df.columns else "—")
    dup_count = df["is_duplicate"].sum() if "is_duplicate" in df.columns else 0
    c4.metric("Duplicate event_ids", int(dup_count))
    errors = (base["Status Code"].astype(str).str.match(r"[45]\d\d")).sum()
    c5.metric("Errors (4xx/5xx)", int(errors))

    # signal quality (for Meta CAPI)
    if "has_email" in df.columns:
        st.markdown("**Match signal coverage**")
        sq1, sq2, sq3, sq4 = st.columns(4)
        total = max(len(df), 1)
        sq1.metric("Has email (em)",  f"{df['has_email'].sum()} / {total}")
        sq2.metric("Has phone (ph)",  f"{df['has_phone'].sum()} / {total}")
        sq3.metric("Has fbp",         f"{df['has_fbp'].sum()} / {total}")
        sq4.metric("Has fbc",         f"{df['has_fbc'].sum()} / {total}")

    # event name breakdown
    if "event_name" in df.columns:
        st.markdown("**Event name breakdown**")
        ec = df["event_name"].value_counts().reset_index()
        ec.columns = ["event_name", "count"]
        st.dataframe(ec, hide_index=True, use_container_width=False)

    # main table
    st.markdown("**Request detail**")
    display_cols = ["status_icon", "Date", "order_id", "event_name", "value", "currency",
                    "event_id", "is_duplicate", "source_url", "Trace Id"]
    display_cols = [c for c in display_cols if c in df.columns]

    styled = df[display_cols].rename(columns={
        "status_icon": "",
        "order_id": "Order ID",
        "event_name": "Event name",
        "value": "Value",
        "currency": "Currency",
        "event_id": "Event ID",
        "is_duplicate": "Duplicate?",
        "source_url": "Source URL",
        "Trace Id": "Trace ID",
    })

    st.dataframe(
        styled,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Duplicate?": st.column_config.CheckboxColumn("Duplicate?"),
            "Value": st.column_config.NumberColumn("Value", format="%.2f"),
        }
    )

    with st.expander("Raw parsed outgoing fields"):
        st.dataframe(out_parsed, use_container_width=True)


# ── INCOMING ───────────────────────────────────────────────────────────────

def show_incoming():
    st.subheader("Incoming responses (Response-Body)")

    df = pd.concat([base, in_parsed], axis=1)

    c1, c2, c3 = st.columns(3)
    c1.metric("Rows", len(df))
    ok = (df["Status Code"].astype(str) == "200").sum() if "Status Code" in df.columns else 0
    c2.metric("200 OK", int(ok))
    err = (df["Status Code"].astype(str).str.match(r"[45]\d\d")).sum() if "Status Code" in df.columns else 0
    c3.metric("Errors", int(err))

    display_cols = ["status_icon", "Date", "Status Code", "events_received", "fbtrace_id", "messages", "error", "Trace Id"]
    display_cols = [c for c in display_cols if c in df.columns]

    st.dataframe(
        df[display_cols].rename(columns={
            "status_icon": "",
            "events_received": "Events received",
            "fbtrace_id": "FB trace ID",
            "messages": "Messages",
            "error": "Error",
            "Trace Id": "Trace ID",
        }),
        hide_index=True,
        use_container_width=True,
    )

    # show raw response bodies that failed
    failed = df[df["Status Code"].astype(str).str.match(r"[45]\d\d")]
    if len(failed):
        with st.expander(f"Failed responses ({len(failed)} rows)"):
            for _, row in failed.iterrows():
                st.code(row.get("Response-Body", "") if "Response-Body" in raw.columns else "", language="json")


# ── RENDER ─────────────────────────────────────────────────────────────────

if mode == "Outgoing requests":
    show_outgoing()
elif mode == "Incoming responses":
    show_incoming()
else:
    show_outgoing()
    st.divider()
    show_incoming()
