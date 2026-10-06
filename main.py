import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright
import time
import traceback
import sys
import asyncio
import asyncio
import io
import os
import threading
from streamlit.runtime.scriptrunner import add_script_run_ctx
from supabase import create_client, Client, ClientOptions
import core_updater

from dotenv import load_dotenv
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

opts = ClientOptions(postgrest_client_timeout=15)
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=opts)

def sync_supplier_to_db(xml_df):
    status_text = st.empty()
    upload_progress = st.progress(0)
    
    def status_callback(text):
        status_text.text(text)
        
    def progress_callback(val):
        upload_progress.progress(val)
        
    def error_callback(err):
        st.error(err)
        
    db_skus = set(st.session_state.df['Артикул поставщика'].astype(str).tolist()) if 'df' in st.session_state and not st.session_state.df.empty else None
    
    core_updater.sync_supplier_to_db(
        xml_df,
        db_skus=db_skus,
        progress_callback=progress_callback,
        status_callback=status_callback,
        error_callback=error_callback
    )
    
    time.sleep(1)
    status_text.empty()
    upload_progress.empty()

def load_data_from_db():
    try:
        all_data = []
        start = 0
        limit = 1000
        while True:
            response = supabase_client.table('products').select('*').order('supplier_sku').range(start, start + limit - 1).execute()
            data = response.data
            if not data:
                break
            all_data.extend(data)
            if len(data) < limit:
                break
            start += limit
            
        db_df = pd.DataFrame(all_data)
        if db_df.empty:
            return pd.DataFrame(columns=["supplier_sku", "name", "brand", "supplier_price", "stock", "weight", "kaspi_sku", "kaspi_name", "kaspi_price", "min_price", "final_price", "preorder"])
            
        if 'preorder' not in db_df.columns:
            db_df['preorder'] = 4
        if 'is_approved' not in db_df.columns:
            db_df['is_approved'] = False
        if 'ai_confidence' not in db_df.columns:
            db_df['ai_confidence'] = 0
            
        # Clean string 'None', 'nan', and empty strings
        db_df = db_df.replace({'None': None, 'none': None, 'NaN': None, 'nan': None, '': None})
        
        # Ensure prefix typing
        db_df['preorder'] = pd.to_numeric(db_df['preorder'], errors='coerce').fillna(4).astype(int)
        db_df['ai_confidence'] = pd.to_numeric(db_df['ai_confidence'], errors='coerce').fillna(0).astype(int)
        db_df['is_approved'] = db_df['is_approved'].fillna(False).astype(bool)

        # Товар не может считаться одобренным, если у него отсутствует Артикул Каспи
        invalid_sku_mask = (
            db_df['kaspi_sku'].isna() | 
            db_df['kaspi_sku'].astype(str).str.strip().str.lower().isin(['', 'none', 'nan', 'null'])
        )
        db_df.loc[invalid_sku_mask, 'is_approved'] = False
        
    except Exception as e:
        st.error(f"Error loading data from Supabase: {e}")
        return pd.DataFrame(columns=["supplier_sku", "name", "brand", "supplier_price", "stock", "weight", "kaspi_sku", "kaspi_name", "kaspi_price", "min_price", "final_price", "preorder", "is_approved", "ai_confidence"])
        
    db_df = db_df.rename(columns={'name': 'supplier_name'})
        
    try:
        with open('stop_brands.txt', 'r', encoding='utf-8') as f:
            stop_brands = {line.strip().lower() for line in f if line.strip()}
    except FileNotFoundError:
        stop_brands = set()

    # Safely filter out stop brands
    db_df = db_df[~db_df['brand'].astype(str).str.lower().str.strip().isin(stop_brands)]

    db_df = db_df.rename(columns={
        'supplier_sku': 'Артикул поставщика',
        'supplier_name': 'Наименование',
        'brand': 'Бренд',
        'supplier_price': 'Цена закупа',
        'stock': 'Остаток',
        'kaspi_sku': 'Артикул Каспи',
        'kaspi_name': 'Название Каспи',
        'kaspi_price': 'Цена на Каспи',
        'weight': 'Вес (кг)',
        'min_price': 'Минимальная цена',
        'final_price': 'Цена реализации',
        'preorder': 'Предзаказ',
        'is_approved': 'Одобрен',
        'ai_confidence': 'Уверенность ИИ'
    })
    return db_df

def save_table_edits():
    if "product_editor" in st.session_state:
        edited_rows = st.session_state["product_editor"].get("edited_rows", {})
        page_skus = st.session_state.get('editor_page_skus', [])
        
        for row_idx, changes in list(edited_rows.items()):
            actual_index = int(row_idx)
            supplier_sku = None
            if actual_index < len(page_skus):
                supplier_sku = page_skus[actual_index]
            elif 'current_page_df' in st.session_state and actual_index < len(st.session_state.current_page_df):
                supplier_sku = st.session_state.current_page_df.iloc[actual_index]['Артикул поставщика']
                
            if not supplier_sku:
                continue
                
            update_data = {}
            if 'Артикул Каспи' in changes:
                new_sku = str(changes['Артикул Каспи']).strip()
                if new_sku.lower() in ('nan', 'none', 'null', ''):
                    new_sku = ''
                update_data["kaspi_sku"] = new_sku
                update_data["is_approved"] = True if new_sku else False
                
            if 'Предзаказ' in changes:
                try:
                    new_preorder = int(changes['Предзаказ'])
                except (ValueError, TypeError):
                    new_preorder = 4
                update_data["preorder"] = new_preorder

            if update_data:
                try:
                    supabase_client.table('products').update(update_data).eq("supplier_sku", supplier_sku).execute()
                except Exception as e:
                    st.error(f"Error updating database: {e}")
                
                # Update the main df safely by finding the matching sku
                mask = st.session_state.df['Артикул поставщика'] == supplier_sku
                if mask.any():
                    if 'kaspi_sku' in update_data:
                        st.session_state.df.loc[mask, 'Артикул Каспи'] = update_data["kaspi_sku"]
                    if 'preorder' in update_data:
                        st.session_state.df.loc[mask, 'Предзаказ'] = update_data["preorder"]
                    if 'is_approved' in update_data:
                        st.session_state.df.loc[mask, 'Одобрен'] = update_data["is_approved"]
                        
                    # Also update current_page_df safely
                    if 'current_page_df' in st.session_state and actual_index < len(st.session_state.current_page_df):
                        if 'kaspi_sku' in update_data:
                            st.session_state.current_page_df.loc[actual_index, 'Артикул Каспи'] = update_data["kaspi_sku"]
                        if 'preorder' in update_data:
                            st.session_state.current_page_df.loc[actual_index, 'Предзаказ'] = update_data["preorder"]
                        if 'is_approved' in update_data:
                            st.session_state.current_page_df.loc[actual_index, 'Одобрен'] = update_data["is_approved"]

# Настройка страницы
st.set_page_config(page_title="Kaspi Manager", layout="wide")
st.title("Управление товарами Kaspi")

@st.cache_data(ttl=600) # Кэшируем данные на 10 минут, чтобы не качать XML при каждом клике
def load_and_parse_xml():
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    def status_callback(text):
        status_text.text(text)
        
    def progress_callback(val):
        progress_bar.progress(val)
        
    def error_callback(err):
        st.error(err)
        
    df = core_updater.load_and_parse_xml(
        progress_callback=progress_callback,
        status_callback=status_callback,
        error_callback=error_callback
    )
    
    status_text.empty()
    progress_bar.empty()
    return df

def calculate_price_for_profit(supplier_price, weight=0, target_profit=0):
    return core_updater.calculate_price_for_profit(supplier_price, weight, target_profit)

async def fetch_batch_kaspi_prices_async(sku_list: list, sku_details: dict, progress_bar, status_text) -> dict:
    prices_dict = {}
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    sem = asyncio.Semaphore(5)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        
        async def process_sku(sku, idx, total):
            async with sem:
                page = await context.new_page()
                # Только блокируем шрифты и картинки, пускаем CSS и JS
                await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font"] else route.continue_())
                
                status_text.text(f"Парсинг артикула {sku} ({idx+1}/{total})...")
                progress_bar.progress((idx + 1) / total)
                
                prices_found = []
                kaspi_name = ""
                try:
                    await page.goto(f"https://kaspi.kz/shop/search/?text={sku}", wait_until="domcontentloaded")
                    
                    try:
                        product_link = page.locator('a[href*="/p/"]').first
                        await product_link.wait_for(state="attached", timeout=15000)
                        product_href = await product_link.get_attribute("href")
                        
                        if product_href:
                            product_url = f"https://kaspi.kz{product_href}" if product_href.startswith("/") else product_href
                            await page.goto(product_url, wait_until="domcontentloaded")
                            await page.wait_for_timeout(2000)
                            
                            kaspi_name = ""
                            try:
                                # Use robust CSS selectors: look for common Kaspi title classes or simply the main h1 tag
                                name_locator = page.locator('.item__name, h1.item__heading, .product-title, h1').first
                                await name_locator.wait_for(state="attached", timeout=5000)
                                kaspi_name = await name_locator.inner_text()
                            except Exception as e:
                                print(f"Name extraction failed for {sku}: {e}")
                                kaspi_name = ""
                            kaspi_name = kaspi_name.strip()
                            
                            tab_locator = page.locator('li[data-tab="offers"], a:has-text("Продавцы"), li:has-text("Продавцы")').first
                            await tab_locator.evaluate("node => node.click()")
                            await page.wait_for_timeout(2000)
                        
                        await page.wait_for_selector("table tbody tr", timeout=15000)
                        rows = await page.locator("table tbody tr").all()
                        
                        for row in rows:
                            cells_count = await row.locator("td").count()
                            if cells_count < 4:
                                continue
                                
                            seller_name = await row.locator("td").nth(0).inner_text()
                            if "ИП EVENTRENT" in seller_name:
                                continue
                                
                            price_text = await row.locator("td").nth(3).inner_text()
                            price_part = price_text.split('₸')[0]
                            just_digits = ''.join(c for c in price_part if c.isdigit())
                            
                            if just_digits:
                                prices_found.append(float(just_digits))
                                
                    except Exception as e:
                        print(f"Navigation/Element error for {sku}: {traceback.format_exc()}")

                    if prices_found:
                        min_price = min(prices_found)
                    else:
                        min_price = 0.0

                    return sku, min_price, kaspi_name

                except Exception as e:
                    print(f"Error fetching Kaspi price for {sku}: {traceback.format_exc()}")
                    return sku, 0.0, ""
                finally:
                    await page.close()
                    await asyncio.sleep(2)
                    
        tasks = [process_sku(sku, i, len(sku_list)) for i, sku in enumerate(sku_list)]
        results = await asyncio.gather(*tasks)
        
        # ====== DATABASE UPDATE PHASE ======
        status_text.text("☁️ Запись новых цен в базу данных...")
        progress_bar.progress(0.0)
        total_results = len([r for r in results if r])
        
        update_count = 0
        for res in results:
            if not res:
                continue
            sku, min_price, kaspi_name = res
            update_count += 1
            
            # Update UI so Streamlit doesn't timeout
            progress_bar.progress(update_count / total_results)
            status_text.text(f"☁️ Сохранение в базу: {sku} ({update_count}/{total_results})")
            
            prices_dict[sku] = {'min_price': min_price, 'kaspi_name': kaspi_name}
            
            # Use local memory instead of Supabase select
            details = sku_details.get(sku, {})
            purchase_price = details.get('purchase_price', 0.0)
            weight = details.get('weight', 0.0)
            
            # 1. Calculate our absolute floor (0 profit) and our target (500 profit)
            breakeven_price = calculate_price_for_profit(supplier_price=purchase_price, weight=weight, target_profit=0)
            target_price = calculate_price_for_profit(supplier_price=purchase_price, weight=weight, target_profit=500)

            # Save the absolute floor to the database as our min_price
            m_price = breakeven_price

            # 1. Safely parse scraped Kaspi price and our calculated min_price
            try:
                k_price_val = float(min_price) if pd.notna(min_price) and min_price else 0.0
            except (ValueError, TypeError):
                k_price_val = 0.0

            try:
                m_price_val = float(m_price) if pd.notna(m_price) and m_price else 0.0
            except (ValueError, TypeError):
                m_price_val = 0.0

            # 2. Strict undercutting logic
            if k_price_val > 0:
                if m_price_val < k_price_val:
                    # Достаем срок предзаказа (если не найден, по умолчанию 1)
                    preorder_days = details.get('preorder', 0)
                    
                    if preorder_days > 4:
                        # Агрессивный демпинг: -20% от цены конкурента для товаров из Китая (предзаказ более 4 дней)
                        calculated_price = k_price_val * 0.8
                        f_price_val = max(m_price_val, calculated_price)
                    else:
                        # Стандартный демпинг: -5 тенге для товаров в наличии / от поставщика (до 4 дней)
                        f_price_val = max(m_price_val, k_price_val - 5)
                else:
                    # Конкурент продает ниже нашего дна. Не опускаемся ниже min_price.
                    f_price_val = m_price_val
            else:
                # No competitors found on Kaspi
                f_price_val = m_price_val

            # 3. Convert back to integer for database saving
            f_price = int(f_price_val)
            m_price = int(m_price_val)

            # 4. Prepare the exact payload for Supabase update
            update_data = {
                "kaspi_price": int(k_price_val) if k_price_val > 0 else 0,
                "min_price": m_price,
                "final_price": f_price
            }
            if kaspi_name and str(kaspi_name).lower() not in ['none', 'nan', '']:
                update_data["kaspi_name"] = kaspi_name
                
            supplier_sku = details.get('supplier_sku')
            try:
                if supplier_sku:
                    supabase_client.table('products').update(update_data).eq("supplier_sku", supplier_sku).execute()
                else:
                    supabase_client.table('products').update(update_data).eq("kaspi_sku", sku).execute()
            except Exception as e:
                print(f"Error updating database for {sku}: {e}")
                
        await browser.close()
            
    return prices_dict

def get_exportable_products(df: pd.DataFrame) -> pd.DataFrame:
    """
    Фильтрует товары для выгрузки в Kaspi XML.
    В выгрузку попадают ТОЛЬКО товары со всеми заполненными обязательными полями:
    1. Артикул Каспи (не пустой, не None, не nan, не null)
    2. Цена реализации (число > 0)
    3. Наименование / Название Каспи (не пустое)
    4. Бренд (не пустой)
    5. Статус: одобрен (Одобрен == True) или ручной товар (m-)
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=df.columns if df is not None else [])

    sku_col = 'Артикул Каспи' if 'Артикул Каспи' in df.columns else 'kaspi_sku'
    approved_col = 'Одобрен' if 'Одобрен' in df.columns else 'is_approved'
    supplier_sku_col = 'Артикул поставщика' if 'Артикул поставщика' in df.columns else 'supplier_sku'
    price_col = 'Цена реализации' if 'Цена реализации' in df.columns else 'final_price'
    kaspi_name_col = 'Название Каспи' if 'Название Каспи' in df.columns else 'kaspi_name'
    name_col = 'Наименование' if 'Наименование' in df.columns else 'name'
    brand_col = 'Бренд' if 'Бренд' in df.columns else 'brand'

    # 1. Валидный Артикул Каспи
    valid_sku = (
        df[sku_col].notna() &
        (df[sku_col].astype(str).str.strip() != '') &
        (~df[sku_col].astype(str).str.strip().str.lower().isin(['none', 'nan', 'null']))
    )

    # 2. Одобрен или ручной товар (префикс m-)
    is_approved = (df[approved_col] == True) if approved_col in df.columns else pd.Series(False, index=df.index)
    is_manual = (
        df[supplier_sku_col].astype(str).str.strip().str.lower().str.startswith('m-')
        if supplier_sku_col in df.columns else pd.Series(False, index=df.index)
    )
    valid_approval = is_approved | is_manual

    # 3. Валидная цена реализации (> 0)
    numeric_prices = pd.to_numeric(df[price_col], errors='coerce') if price_col in df.columns else pd.Series(0, index=df.index)
    valid_price = numeric_prices.notna() & (numeric_prices > 0)

    # 4. Валидное наименование / модель (хотя бы одно из полей не пустое)
    has_kaspi_name = (
        df[kaspi_name_col].notna() &
        (df[kaspi_name_col].astype(str).str.strip() != '') &
        (~df[kaspi_name_col].astype(str).str.strip().str.lower().isin(['none', 'nan', 'null']))
    ) if kaspi_name_col in df.columns else pd.Series(False, index=df.index)

    has_supplier_name = (
        df[name_col].notna() &
        (df[name_col].astype(str).str.strip() != '') &
        (~df[name_col].astype(str).str.strip().str.lower().isin(['none', 'nan', 'null']))
    ) if name_col in df.columns else pd.Series(False, index=df.index)

    valid_model = has_kaspi_name | has_supplier_name

    # 5. Валидный бренд
    valid_brand = (
        df[brand_col].notna() &
        (df[brand_col].astype(str).str.strip() != '') &
        (~df[brand_col].astype(str).str.strip().str.lower().isin(['none', 'nan', 'null']))
    ) if brand_col in df.columns else pd.Series(False, index=df.index)

    mask = valid_sku & valid_approval & valid_price & valid_model & valid_brand
    return df[mask].copy()

def generate_kaspi_xml(df: pd.DataFrame, merchant_id="30391602", city_id="750000000", store_id="PP1") -> bytes:
    from datetime import datetime
    import xml.etree.ElementTree as ET
    
    filtered_df = get_exportable_products(df)
    
    # Create root element with exact namespaces
    root = ET.Element("kaspi_catalog", {
        "xmlns": "kaspiShopping",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": "kaspiShopping http://kaspi.kz/kaspishopping.xsd",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    
    ET.SubElement(root, "company").text = str(merchant_id).strip()
    ET.SubElement(root, "merchantid").text = str(merchant_id).strip()
    offers = ET.SubElement(root, "offers")
    
    # Поддерживаем один или несколько кодов складов (через запятую)
    stores = [s.strip() for s in str(store_id).split(',') if s.strip()]
    if not stores:
        stores = ["PP1"]
        
    for _, row in filtered_df.iterrows():
        sku_val = row.get('Артикул Каспи', row.get('kaspi_sku', ''))
        if pd.isna(sku_val) or sku_val is None:
            continue
        sku = str(sku_val).strip()
        if not sku or sku.lower() in ('none', 'nan', 'null'):
            continue

        # Model (prefer Kaspi name, fallback to Supplier name)
        model = str(row.get('Название Каспи', row.get('kaspi_name', '')) or '').strip()
        if not model or model.lower() in ('none', 'nan', 'null'):
            model = str(row.get('Наименование', row.get('name', '')) or '').strip()
        if not model or model.lower() in ('none', 'nan', 'null'):
            continue

        # Brand
        brand = str(row.get('Бренд', row.get('brand', '')) or '').strip()
        if not brand or brand.lower() in ('none', 'nan', 'null'):
            continue

        # City Prices
        try:
            price_val = int(round(float(row.get('Цена реализации', row.get('final_price', 0)))))
        except (ValueError, TypeError):
            price_val = 0
            
        if price_val <= 0:
            continue

        offer = ET.SubElement(offers, "offer", sku=sku)
        ET.SubElement(offer, "model").text = model
        ET.SubElement(offer, "brand").text = brand
            
        # Availabilities
        availabilities = ET.SubElement(offer, "availabilities")
        try:
            stock = float(row.get('Остаток', row.get('stock', 0)))
        except (ValueError, TypeError):
            stock = 0.0
            
        # Kaspi требует строго целое число для stockCount (например, 30, а не 30.0)
        stock_int = max(0, int(round(stock)))
        available_str = "yes" if stock_int > 0 else "no"
        
        # Срок предзаказа (preOrder):
        # Для ручных товаров (с префиксом 'm-') берем значение из поля 'Предзаказ'
        # Для всех остальных товаров по умолчанию 4
        supplier_sku = str(row.get('Артикул поставщика', row.get('supplier_sku', '')) or '').strip()
        is_manual = supplier_sku.lower().startswith('m-')
        if is_manual:
            preorder_raw = row.get('Предзаказ', row.get('preorder'))
            if pd.notna(preorder_raw) and str(preorder_raw).strip() != '':
                try:
                    preorder_val = int(float(preorder_raw))
                except (ValueError, TypeError):
                    preorder_val = 0
            else:
                preorder_val = 0
        else:
            preorder_val = 4
        
        for st_code in stores:
            avail_attrs = {
                "available": available_str,
                "storeId": st_code,
                "stockCount": str(stock_int)
            }
            # preOrder передается только если он больше 0 (стандарт Kaspi)
            if preorder_val > 0:
                avail_attrs["preOrder"] = str(preorder_val)
                
            ET.SubElement(availabilities, "availability", avail_attrs)
            
        cityprices = ET.SubElement(offer, "cityprices")
        ET.SubElement(cityprices, "cityprice", cityId=str(city_id).strip()).text = str(price_val)
        
    # Форматирование XML с отступами и стандартной кодировкой UTF-8 без BOM
    ET.indent(root, space="    ")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode')
    return xml_str.encode('utf-8')

# Кнопка для ручного обновления базы от поставщика
if st.button("🔄 Скачать и обновить прайс Al-Style (Синхронизация)"):
    st.write("Скачивание и обработка прайса Al-Style...")
    xml_df = load_and_parse_xml()
    if not xml_df.empty:
        sync_supplier_to_db(xml_df)
        # Force reload data from DB after sync
        st.session_state.df = load_data_from_db()
        st.success("База успешно обновлена!")
        st.rerun()

# Инициализация DataFrame в сессии
if 'df' not in st.session_state:
    st.session_state.df = load_data_from_db()

df = st.session_state.df

if not df.empty:
    st.success(f"Успешно загружено товаров (с остатком >= 2): {len(df)}")
    
    with st.expander("📦 Добавить свой товар (ручной ввод)"):
        with st.form("custom_product_form"):
            col1, col2 = st.columns(2)
            with col1:
                custom_sku = st.text_input("Артикул / Код товара (будет добавлен префикс m-)")
                custom_name = st.text_input("Наименование (обязательно)")
                custom_brand = st.text_input("Бренд")
            with col2:
                custom_price = st.number_input("Цена закупа", min_value=0.0, format="%.2f")
                custom_stock = st.number_input("Остаток на складе", min_value=0, step=1)
                custom_weight = st.number_input("Вес в кг", min_value=0.0, format="%.3f")
                custom_kaspi_sku = st.text_input("Артикул Каспи (необязательно)")
                custom_preorder = st.number_input("Срок предзаказа (дней)", min_value=0, max_value=30, value=15)
                
            submitted = st.form_submit_button("Сохранить в базу")
            
            if submitted:
                if custom_sku.strip() and custom_name.strip():
                    breakeven_price = calculate_price_for_profit(supplier_price=custom_price, weight=custom_weight, target_profit=0)
                    target_price = calculate_price_for_profit(supplier_price=custom_price, weight=custom_weight, target_profit=500)
                    m_price = breakeven_price
                    f_price = target_price
                    
                    # Принудительно добавляем m-, чтобы система знала, что это ручной товар
                    sku_val = custom_sku.strip()
                    if not sku_val.lower().startswith('m-'):
                        sku_val = f"m-{sku_val}"
                        
                    try:
                        product_data = {
                            "supplier_sku": sku_val,
                            "name": custom_name.strip(),
                            "brand": custom_brand.strip(),
                            "supplier_price": custom_price,
                            "stock": custom_stock,
                            "kaspi_sku": custom_kaspi_sku.strip(),
                            "weight": custom_weight,
                            "min_price": m_price,
                            "final_price": f_price,
                            "preorder": custom_preorder,
                            "is_approved": bool(custom_kaspi_sku.strip())
                        }
                        
                        existing = supabase_client.table('products').select('*').eq('supplier_sku', sku_val).execute()
                        if not existing.data or len(existing.data) == 0:
                            product_data["kaspi_price"] = 0.0
                        else:
                            if not custom_kaspi_sku.strip() and existing.data[0].get('kaspi_sku'):
                                product_data["kaspi_sku"] = existing.data[0]['kaspi_sku']
                            
                        supabase_client.table('products').upsert(product_data).execute()
                        st.success("Товар успешно добавлен!")
                        st.session_state.df = load_data_from_db()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error saving product: {e}")
                else:
                    st.error("Артикул и Наименование обязательны для заполнения!")

    with st.expander("🤖 Модерация ИИ (проверка найденных артикулов)"):
        # Filter df where kaspi_sku is not null but Одобрен is False
        mod_mask = (
            df['Артикул Каспи'].notna() & 
            (df['Артикул Каспи'].astype(str).str.strip() != '') & 
            (df['Артикул Каспи'].astype(str).str.lower() != 'none') &
            (df['Артикул Каспи'].astype(str).str.lower() != 'nan') &
            (df.get('Одобрен', pd.Series([False]*len(df))) == False) &
            (~df['Артикул поставщика'].astype(str).str.lower().str.startswith('m-'))
        )
        mod_df = df[mod_mask].copy()
        
        if mod_df.empty:
            st.info("Нет товаров, требующих проверки ИИ.")
        else:
            mod_display = mod_df[['Артикул поставщика', 'Наименование', 'Бренд', 'Цена закупа', 'Артикул Каспи', 'Уверенность ИИ']].copy()
            mod_display['Одобрить'] = False
            
            st.write(f"Ожидают проверки: {len(mod_df)} товаров")
            
            edited_mod_df = st.data_editor(
                mod_display,
                column_config={
                    "Одобрить": st.column_config.CheckboxColumn("Одобрить", help="Отметьте, чтобы подтвердить артикул"),
                    "Уверенность ИИ": st.column_config.ProgressColumn("Уверенность ИИ", format="%d%%", min_value=0, max_value=100)
                },
                disabled=["Артикул поставщика", "Наименование", "Бренд", "Цена закупа", "Артикул Каспи", "Уверенность ИИ"],
                hide_index=True,
                key="mod_editor"
            )
            
            if st.button("Сохранить проверенные"):
                approved_skus = edited_mod_df[edited_mod_df['Одобрить'] == True]['Артикул поставщика'].tolist()
                if approved_skus:
                    try:
                        supabase_client.table('products').update({"is_approved": True}).in_("supplier_sku", approved_skus).execute()
                        st.success(f"Одобрено {len(approved_skus)} товаров!")
                        st.session_state.df = load_data_from_db()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка сохранения: {e}")
                else:
                    st.warning("Ни один товар не отмечен для одобрения.")

    # Генерация XML файла для Каспи
    st.subheader("📄 Экспорт прайс-листа в Kaspi XML")
    
    exportable_df = get_exportable_products(df)
    st.info(f"📊 Готово к выгрузке в Kaspi: **{len(exportable_df)}** товаров (все обязательные поля заполнены: артикул Каспи, цена > 0, название, бренд).")
    
    with st.expander("⚙️ Настройки выгрузки Kaspi XML", expanded=False):
        col_cfg1, col_cfg2, col_cfg3 = st.columns(3)
        with col_cfg1:
            merchant_id_input = st.text_input("Merchant ID", value="30391602", help="Ваш ID продавца в Kaspi")
        with col_cfg2:
            store_id_input = st.text_input("Код склада (storeId)", value="PP1", help="Код склада из кабинета Kaspi ('Склады и магазины'). По умолчанию 'PP1'. Можно через запятую, например 'PP1, PP2'.")
        with col_cfg3:
            city_id_input = st.text_input("Код города (cityId)", value="750000000", help="Код города (750000000 для Алматы)")

    actual_merchant_id = merchant_id_input.strip() if 'merchant_id_input' in locals() and merchant_id_input.strip() else "30391602"
    actual_store_id = store_id_input.strip() if 'store_id_input' in locals() and store_id_input.strip() else "PP1"
    actual_city_id = city_id_input.strip() if 'city_id_input' in locals() and city_id_input.strip() else "750000000"

    xml_data = generate_kaspi_xml(
        df, 
        merchant_id=actual_merchant_id, 
        city_id=actual_city_id, 
        store_id=actual_store_id
    )
    
    col_dl, col_pub = st.columns([1, 2])
    with col_dl:
        st.download_button(
            label="📥 Скачать XML для Kaspi",
            data=xml_data,
            file_name="kaspi_prices.xml",
            mime="application/xml; charset=utf-8",
            type="primary"
        )
        
    with col_pub:
        if st.button("🌐 Опубликовать XML по ссылке"):
            with st.spinner("Загрузка в облако Supabase..."):
                try:
                    res = supabase_client.storage.from_("kaspi").upload(
                        path="kaspi_prices.xml", 
                        file=xml_data, 
                        file_options={"upsert": "true", "content-type": "application/xml; charset=utf-8"}
                    )
                    public_url = supabase_client.storage.from_("kaspi").get_public_url("kaspi_prices.xml")
                    st.success("✅ XML файл успешно опубликован!")
                    st.info("Скопируй эту ссылку и вставь в настройки автоматического обновления Каспи:")
                    st.code(public_url)
                except Exception as e:
                    st.error(f"❌ Ошибка публикации. Подробности: {e}")

    with st.expander("👁️ Предпросмотр сгенерированного XML (первые 50 строк)", expanded=False):
        xml_text = xml_data.decode('utf-8', errors='replace')
        first_50_lines = "\n".join(xml_text.splitlines()[:50])
        st.code(first_50_lines, language="xml")

    st.markdown("---")
    
    # Поиск и фильтрация
    search_query = st.text_input("🔍 Поиск по артикулу, названию или бренду", "")
    
    if search_query:
        search_lower = search_query.lower()
        mask = (
            df['Артикул поставщика'].astype(str).str.lower().str.contains(search_lower) |
            df['Наименование'].astype(str).str.lower().str.contains(search_lower) |
            df['Бренд'].astype(str).str.lower().str.contains(search_lower) |
            df['Артикул Каспи'].astype(str).str.lower().str.contains(search_lower)
        )
        display_df = df[mask].copy()
    else:
        display_df = df.copy()

    # Проверяем изменение поиска для обновления стабильного порядка
    search_changed = (st.session_state.get('last_search_query', None) != search_query)
    
    if 'display_order_skus' not in st.session_state or search_changed:
        st.session_state.last_search_query = search_query
        # Стабильная сортировка: пустые Артикулы Каспи наверх, внутри сортировка по Артикулу поставщика
        has_kaspi = display_df['Артикул Каспи'].notna() & \
                    (display_df['Артикул Каспи'].astype(str).str.strip() != '') & \
                    (display_df['Артикул Каспи'].astype(str).str.lower() != 'none') & \
                    (display_df['Артикул Каспи'].astype(str).str.lower() != 'nan')
        unlinked = display_df[~has_kaspi].sort_values('Артикул поставщика')
        linked = display_df[has_kaspi].sort_values('Артикул поставщика')
        ordered = pd.concat([unlinked, linked])
        st.session_state.display_order_skus = ordered['Артикул поставщика'].tolist()

    # Применяем стабильный порядок к display_df
    # Строки НЕ перемещаются во время редактирования ячеек!
    order_map = {sku: idx for idx, sku in enumerate(st.session_state.display_order_skus)}
    display_df = display_df[display_df['Артикул поставщика'].isin(order_map)].copy()
    display_df['__order_rank'] = display_df['Артикул поставщика'].map(order_map)
    display_df = display_df.sort_values('__order_rank').drop(columns=['__order_rank']).reset_index(drop=True)

    col_page, col_refresh = st.columns([3, 1])
    page_size = 50
    total_pages = max(1, len(display_df) // page_size + (1 if len(display_df) % page_size > 0 else 0))
    with col_page:
        page_number = st.number_input("Страница", min_value=1, max_value=total_pages, value=1)
    with col_refresh:
        st.write("")
        st.write("")
        if st.button("🔄 Обновить порядок таблицы"):
            if 'display_order_skus' in st.session_state:
                del st.session_state['display_order_skus']
            st.rerun()

    start_idx = (page_number - 1) * page_size
    end_idx = start_idx + page_size
    current_page = display_df.iloc[start_idx:end_idx].copy()
    current_page.reset_index(drop=True, inplace=True)
    
    # Сохраняем точный снимок Артикулов поставщика для текущей страницы:
    st.session_state.editor_page_skus = current_page['Артикул поставщика'].tolist()

    price_cols = ['Цена закупа', 'Цена на Каспи', 'Минимальная цена', 'Цена реализации']
    for col in price_cols:
        if col in current_page.columns:
            temp_numeric = pd.to_numeric(current_page[col], errors='coerce')
            try:
                # Пробуем округлить и привести к целому (Int64 поддерживает NaN)
                current_page[col] = temp_numeric.round().astype('Int64')
            except TypeError:
                # Если данные сопротивляются безопасному касту, оставляем их как float64
                current_page[col] = temp_numeric.astype('float64')
            
    st.session_state.current_page_df = current_page

    def highlight_prices(row):
        styles = [''] * len(row)
        try:
            m_val = float(row['Минимальная цена'])
            k_val = float(row['Цена на Каспи'])
            
            if pd.notna(m_val) and pd.notna(k_val) and k_val > 0:
                # Find the index of the min_price column safely
                if 'Минимальная цена' in row.index:
                    col_idx = row.index.get_loc('Минимальная цена')
                    if m_val < k_val:
                        styles[col_idx] = 'color: #006400;' # Dark Green
                    elif m_val > k_val:
                        styles[col_idx] = 'color: #FF0000;' # Red
        except (ValueError, TypeError):
            pass
        return styles

    styled_df = st.session_state.current_page_df.style.apply(highlight_prices, axis=1)

    st.data_editor(
        styled_df, 
        column_order=["Артикул поставщика", "Наименование", "Бренд", "Цена закупа", "Остаток", "Вес (кг)", "Предзаказ", "Артикул Каспи", "Название Каспи", "Цена на Каспи", "Минимальная цена", "Цена реализации"],
        width='stretch',
        height=1800,
        hide_index=True,
        disabled=["Артикул поставщика", "Наименование", "Бренд", "Цена закупа", "Остаток", "Вес (кг)", "Название Каспи", "Цена на Каспи", "Минимальная цена", "Цена реализации"],
        key="product_editor",
        on_change=save_table_edits
    )
            
    # Кнопка для запуска парсера
    if st.button("Запросить цены Каспи"):
        st.write("Запускаем сбор цен...")
        
        # Strictly filter out empty strings, pandas NA/NaN, and string representations of 'None' or 'NaN'
        valid_rows = st.session_state.df[
            (st.session_state.df['Артикул Каспи'].notna()) & 
            (st.session_state.df['Артикул Каспи'].astype(str).str.strip() != '') & 
            (st.session_state.df['Артикул Каспи'].astype(str).str.lower() != 'none') &
            (st.session_state.df['Артикул Каспи'].astype(str).str.lower() != 'nan')
        ]
        
        if valid_rows.empty:
            st.warning("Нет заполненных артикулов Каспи для парсинга.")
        else:
            skus_to_fetch = valid_rows['Артикул Каспи'].astype(str).str.strip().tolist()
            
            # Prepare local data to avoid DB selects later
            sku_details = {}
            for _, row in valid_rows.iterrows():
                sku = str(row['Артикул Каспи']).strip()
                supplier_sku = str(row['Артикул поставщика']).strip()
                sku_details[sku] = {
                    'supplier_sku': supplier_sku,
                    'purchase_price': float(row['Цена закупа']) if pd.notna(row['Цена закупа']) else 0.0,
                    'weight': float(row['Вес (кг)']) if pd.notna(row['Вес (кг)']) else 0.0,
                    'preorder': int(row['Предзаказ']) if pd.notna(row['Предзаказ']) else 4
                }
                
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Запускаем парсер в отдельном потоке, чтобы не вешать WebSocket Streamlit
            result_container = {}

            def background_task():
                if sys.platform == 'win32':
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                res = loop.run_until_complete(fetch_batch_kaspi_prices_async(skus_to_fetch, sku_details, progress_bar, status_text))
                result_container['data'] = res

            thread = threading.Thread(target=background_task)
            add_script_run_ctx(thread) # Позволяет фоновому потоку обновлять st.progress и st.empty
            thread.start()

            # Главный поток просто ждет, пропуская пинги от браузера, чтобы избежать тайм-аута
            while thread.is_alive():
                time.sleep(0.5)

            prices_dict = result_container.get('data', {})
            
            # Перезагружаем из БД, чтобы обновить UI
            st.session_state.df = load_data_from_db()
            if 'display_order_skus' in st.session_state:
                del st.session_state['display_order_skus']
            
            status_text.text("Парсинг завершен!")
            st.success("Цены обновлены.")
            st.rerun()

else:
    st.warning("Нет данных для отображения. Проверь структуру XML-файла.")
