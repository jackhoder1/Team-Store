"""
Team Store Pack List
--------------------
Pick an order export CSV -> builds a packing-list PDF of every order with
embroidered (EMB) items, with a product photo for each item, and copies the
EMB totals to the clipboard.

Images come from Shopify (read-only):
  1. Shopify Admin GraphQL API, looked up by exact SKU (needs a token)
  2. Fallback: the public rugbyimports.com site search (no token needed)

The token is NOT stored in this file. It is read from, in order:
  - the SHOPIFY_TOKEN environment variable
  - team_store_config.json next to this script / the .exe
If neither has one, the app asks for it once and saves it to that file.
The same token used by the Price Comparator app works here.
"""

import json
import os
import sys
from datetime import datetime
from io import BytesIO
from tkinter import Tk, filedialog, messagebox, simpledialog

import pandas as pd
import pyperclip
import requests
from bs4 import BeautifulSoup
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

# =====================================
# CONFIGURATION
# =====================================
STORE_URL = "https://rugbyimports1.myshopify.com"
PUBLIC_SITE = "https://www.rugbyimports.com"
API_VERSION = "2026-04"   # Shopify retires versions after ~12 months; bump when needed


def app_dir() -> str:
    """Folder holding the script, or the .exe when packaged with PyInstaller."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "team_store_config.json")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        print(f"Could not save config: {e}")


def get_token(cfg: dict) -> str:
    token = os.environ.get("SHOPIFY_TOKEN", "").strip() or cfg.get("shopify_token", "").strip()
    if token:
        return token
    token = (simpledialog.askstring(
        "Shopify token",
        "Paste the Shopify Admin API token (starts with shpat_).\n"
        "Same one the Price Comparator uses.\n\n"
        "Leave blank to skip and use the public website for images.",
    ) or "").strip()
    if token:
        cfg["shopify_token"] = token
        save_config(cfg)
    return token


# =====================================
# IMAGE LOOKUP (read-only)
# =====================================
product_cache: dict = {}
api_state = {"token": "", "disabled_reason": ""}

VARIANT_QUERY = """
query($q: String!) {
  productVariants(first: 1, query: $q) {
    edges { node { sku image { url } product { featuredImage { url } } } }
  }
}
"""


def image_from_api(sku: str):
    """Exact SKU lookup through the Admin GraphQL API."""
    if not api_state["token"] or api_state["disabled_reason"]:
        return None
    url = f"{STORE_URL}/admin/api/{API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": api_state["token"], "Content-Type": "application/json"}
    body = {"query": VARIANT_QUERY, "variables": {"q": f'sku:"{sku}"'}}
    try:
        r = requests.post(url, json=body, headers=headers, timeout=10)
        if r.status_code in (401, 403):
            # Bad/expired token: stop hammering the API for the rest of this run.
            api_state["disabled_reason"] = f"Shopify API rejected the token (HTTP {r.status_code})."
            return None
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            print(f"API error for {sku}: {data['errors']}")
            return None
        edges = (data.get("data") or {}).get("productVariants", {}).get("edges", [])
        if edges:
            node = edges[0]["node"]
            img = node.get("image") or (node.get("product") or {}).get("featuredImage")
            if img:
                return img.get("url")
    except Exception as e:
        print(f"API error for {sku}: {e}")
    return None


def image_from_website(sku: str):
    """Fallback: public site search -> product .json -> matching variant image."""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(f"{PUBLIC_SITE}/search", params={"type": "product", "q": sku},
                         headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        handles = []
        for a in soup.select('a[href*="/products/"]'):
            h = a.get("href", "").split("/products/")[1].split("?")[0].split("#")[0].strip("/")
            if h and h not in handles:
                handles.append(h)
        for handle in handles[:5]:
            rj = requests.get(f"{PUBLIC_SITE}/products/{handle}.json", headers=headers, timeout=10)
            if rj.status_code != 200:
                continue
            product = rj.json().get("product", {})
            variant = next((v for v in product.get("variants", [])
                            if str(v.get("sku", "")).strip().upper() == sku), None)
            if not variant:
                continue
            images = product.get("images", [])
            img = next((i for i in images if variant.get("id") in i.get("variant_ids", [])), None)
            img = img or (images[0] if images else None)
            if img and img.get("src"):
                src = img["src"]
                return "https:" + src if src.startswith("//") else src
    except Exception as e:
        print(f"Website lookup error for {sku}: {e}")
    return None


def get_variant_image(sku: str):
    if sku in product_cache:
        return product_cache[sku]
    url = image_from_api(sku) or image_from_website(sku)
    product_cache[sku] = url
    return url


# =====================================
# CSV LOADING
# =====================================
def load_orders(file_path: str) -> pd.DataFrame:
    # Read by column position (range(35)) to cope with messy exports.
    df_raw = pd.read_csv(file_path, header=None, names=range(35), dtype=str,
                         engine="python").fillna("")
    cleaned = []
    for _, row in df_raw.iterrows():
        r = row.tolist()
        order_num = str(r[1]).strip().upper()
        if not order_num or "ORDER" in order_num:
            continue  # blank lines / header rows
        item_sku = str(r[2]).strip().upper()
        master_sku = str(r[3]).strip().upper()
        cost = str(r[4]).strip()
        if "OUTLINE" in str(r[6]).upper():
            name = f"{r[5]}, {r[6]}".strip()
            qty = str(r[7]).strip()
        else:
            name = str(r[5]).strip()
            qty = str(r[6]).strip()
        cleaned.append({"Order Number": order_num, "Item SKU": item_sku,
                        "Master SKU": master_sku, "Item Name": name,
                        "Order Quantity": qty, "Cost": cost})
    return pd.DataFrame(cleaned, columns=["Order Number", "Item SKU", "Master SKU",
                                          "Item Name", "Order Quantity", "Cost"])


# =====================================
# PDF
# =====================================
def build_pdf(df: pd.DataFrame, output_pdf: str) -> tuple[int, list]:
    doc = SimpleDocTemplate(output_pdf, pagesize=letter)
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle("TableCell", parent=styles["Normal"], fontSize=9, leading=10)
    order_style = ParagraphStyle("OrderStyle", parent=styles["Heading1"], fontSize=26, spaceAfter=12)
    item_style = ParagraphStyle("ItemStyle", parent=styles["Heading2"], fontSize=14, spaceAfter=6)
    big_style = ParagraphStyle("BigStyle", fontSize=18, leading=22)

    elements, missing_images, orders = [], [], 0
    for order_number, order_df in df.groupby("Order Number"):
        is_emb = order_df["Item SKU"].str.contains("EMB") | order_df["Master SKU"].str.contains("EMB")
        valid_skus = order_df[is_emb]["Item SKU"].unique()
        if not len(valid_skus):
            continue
        orders += 1

        elements.append(Paragraph(f"ORDER {order_number}", order_style))
        emb_rows = order_df[order_df["Master SKU"].str.contains("EMB")]
        if not emb_rows.empty:
            team_name = " ".join(str(emb_rows.iloc[0]["Item Name"]).split()[:2])
            elements.append(Paragraph(team_name, big_style))
        elements.append(Spacer(1, 0.3 * inch))

        for i, sku in enumerate(valid_skus):
            if i and i % 2 == 0:
                elements.append(PageBreak())

            elements.append(Paragraph(sku, item_style))
            img_url = get_variant_image(sku)
            added = False
            if img_url:
                try:
                    img_data = requests.get(img_url, timeout=10).content
                    image = Image(BytesIO(img_data), width=1.3 * inch, height=1.3 * inch)
                    image.hAlign = "LEFT"
                    elements += [image, Spacer(1, 0.15 * inch)]
                    added = True
                except Exception as e:
                    print(f"Image download failed for {sku}: {e}")
            if not added:
                missing_images.append(sku)
                elements.append(Spacer(1, 1.45 * inch))

            table_data = [["Type", "SKU", "Description", "Qty"]]
            for _, row in order_df[order_df["Item SKU"] == sku].iterrows():
                label = "Embroidery SKU" if "EMB" in str(row["Master SKU"]) else "Product SKU"
                table_data.append([label, row["Master SKU"],
                                   Paragraph(row["Item Name"], cell_style), row["Order Quantity"]])
            table = Table(table_data, colWidths=[1.1 * inch, 1.5 * inch, 3.2 * inch, 0.6 * inch])
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elements += [table, Spacer(1, 0.4 * inch)]

        elements.append(PageBreak())

    if elements:
        doc.build(elements)
    return orders, missing_images


def emb_clipboard_text(df: pd.DataFrame) -> str:
    df_clip = df[df["Master SKU"].str.contains("EMB")].copy()
    if df_clip.empty:
        return ""
    df_clip["Order Quantity"] = pd.to_numeric(df_clip["Order Quantity"], errors="coerce").fillna(0)
    grouped = df_clip.groupby("Master SKU").agg({"Order Quantity": "sum", "Cost": "first"}).reset_index()
    return "\n".join(f"{r['Master SKU']},{int(r['Order Quantity'])},Each,{r['Cost']},$0,0"
                     for _, r in grouped.iterrows())


# =====================================
# MAIN
# =====================================
def main():
    root = Tk()
    root.withdraw()

    file_path = filedialog.askopenfilename(title="Select CSV File", filetypes=[("CSV Files", "*.csv")])
    if not file_path:
        return

    api_state["token"] = get_token(load_config())

    df = load_orders(file_path)
    output_pdf = f"Team Store {datetime.now().strftime('%m-%d-%Y')}.pdf"  # same place as before
    orders, missing = build_pdf(df, output_pdf)
    if not orders:
        messagebox.showwarning("Nothing to print", "No orders with EMB items were found in that file.")
        return

    notes = []
    clip = emb_clipboard_text(df)
    if clip:
        try:
            pyperclip.copy(clip)
            notes.append("EMB items copied to clipboard.")
        except Exception as e:
            notes.append(f"Clipboard copy failed: {e}")
    if api_state["disabled_reason"]:
        notes.append(api_state["disabled_reason"] + " Used the public website instead.\n"
                     f"To fix, update the token in {CONFIG_PATH} (or delete that file to be asked again).")
    if missing:
        shown = ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else "")
        notes.append(f"No image found for {len(missing)} item(s): {shown}")

    try:
        os.startfile(output_pdf)  # Windows only
    except AttributeError:
        pass
    messagebox.showinfo("Success", f"PDF created: {os.path.basename(output_pdf)} ({orders} orders)\n\n"
                        + "\n\n".join(notes))


if __name__ == "__main__":
    main()
