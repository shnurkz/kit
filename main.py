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

SUPABASE_URL = "https://akrygxdwrwyoaxdsjefs.supabase.co"
SUPABASE_KEY = "sb_secret_-6yiRL3AwNCJ3EHqtPW-ww_oiVqN6f_"
opts = ClientOptions(postgrest_client_timeout=15)
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=opts)

def sync_supplier_to_db(xml_df):
    status_text = st.empty()
    upload_progress = st.progress(0)
    status_text.text(f"☁️ Начинаю отправку {len(xml_df)} товаров в базу...")
    
    BATCH_SIZE = 200
    batch = []
    uploaded_count = 0
    total_products = len(xml_df)
    
    # 1. Track parsed SKUs
    xml_skus = set(xml_df['Артикул поставщика'].astype(str).tolist())
    
    for _, row in xml_df.iterrows():
        product_data = {
            "supplier_sku": str(row['Артикул поставщика']),
            "name": str(row['Наименование']),
            "brand": str(row['Бренд']),
            "supplier_price": float(row['Цена закупа']),
            "stock": float(row['Остаток']),
            "weight": float(row['Вес (кг)']),
            "preorder": 1
        }
        batch.append(product_data)
        
        if len(batch) >= BATCH_SIZE:
            try:
                supabase_client.table('products').upsert(batch).execute()
                uploaded_count += len(batch)
                upload_progress.progress(min(uploaded_count / total_products, 1.0))
                status_text.text(f"☁️ Отправлено в облако: {uploaded_count} из {total_products}...")
                batch = []
            except Exception as e:
                st.error(f"❌ Ошибка сети при отправке данных: {e}")
                break
                
    if batch:
        try:
            supabase_client.table('products').upsert(batch).execute()
            uploaded_count += len(batch)
            upload_progress.progress(1.0)
            status_text.text(f"☁️ Отправлено в облако: {uploaded_count} из {total_products}...")
        except Exception as e:
            st.error(f"❌ Ошибка при отправке последней пачки: {e}")

    # 2. Find Missing SKUs
    if 'df' in st.session_state and not st.session_state.df.empty:
        db_skus = set(st.session_state.df['Артикул поставщика'].astype(str).tolist())
    else:
        # Fallback: fetch just the SKUs from Supabase if df is not available
        db_skus = set()
        start = 0
        limit = 1000
        while True:
            try:
                response = supabase_client.table('products').select('supplier_sku').range(start, start + limit - 1).execute()
                data = response.data
                if not data: break
                for d in data:
                    db_skus.add(str(d.get('supplier_sku', '')))
                if len(data) < limit: break
                start += limit
            except Exception:
                break
                
    # 1. Get the raw missing SKUs (in DB but not in XML)
    raw_missing_skus = list(db_skus - xml_skus)

    # 2. Filter out manual SKUs (starting with 'm-' or 'M-')
    missing_skus = [
        sku for sku in raw_missing_skus 
        if not str(sku).lower().startswith('m-')
    ]
    
    # 3. Zero Out Stock for Missing SKUs in Supabase
    if missing_skus:
        status_text.text(f"🧹 Обнуление остатков для {len(missing_skus)} снятых с продажи товаров...")
        chunk_size = 200
        for i in range(0, len(missing_skus), chunk_size):
            chunk = missing_skus[i:i + chunk_size]
            try:
                supabase_client.table('products').update({"stock": 0}).in_("supplier_sku", chunk).execute()
            except Exception as e:
                print(f"Error zeroing stock for chunk: {e}")
            
    time.sleep(1)
    status_text.empty()
    upload_progress.empty()

def load_data_from_db():
    try:
        all_data = []
        start = 0
        limit = 1000
        while True:
            response = supabase_client.table('products').select('*').range(start, start + limit - 1).execute()
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
            db_df['preorder'] = 1
        if 'is_approved' not in db_df.columns:
            db_df['is_approved'] = False
        if 'ai_confidence' not in db_df.columns:
            db_df['ai_confidence'] = 0
            
        # Clean string 'None', 'nan', and empty strings
        db_df = db_df.replace({'None': None, 'none': None, 'NaN': None, 'nan': None, '': None})
        
        # Ensure prefix typing
        db_df['preorder'] = pd.to_numeric(db_df['preorder'], errors='coerce').fillna(1).astype(int)
        db_df['ai_confidence'] = pd.to_numeric(db_df['ai_confidence'], errors='coerce').fillna(0).astype(int)
        db_df['is_approved'] = db_df['is_approved'].fillna(False).astype(bool)
        
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
        for row_idx, changes in edited_rows.items():
            if 'current_page_df' in st.session_state and row_idx < len(st.session_state.current_page_df):
                actual_index = int(row_idx)
                supplier_sku = st.session_state.current_page_df.iloc[actual_index]['Артикул поставщика']
                
                update_data = {}
                if 'Артикул Каспи' in changes:
                    new_sku = str(changes['Артикул Каспи']).strip()
                    if new_sku == 'nan': new_sku = ''
                    update_data["kaspi_sku"] = new_sku
                    update_data["is_approved"] = True
                    
                if 'Предзаказ' in changes:
                    try:
                        new_preorder = int(changes['Предзаказ'])
                    except (ValueError, TypeError):
                        new_preorder = 1
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

# Настройка страницы
st.set_page_config(page_title="Kaspi Manager", layout="wide")
st.title("Управление товарами Kaspi")

@st.cache_data(ttl=600) # Кэшируем данные на 10 минут, чтобы не качать XML при каждом клике
def load_and_parse_xml():
    url = "https://apifeed.al-style.kz/feed.xml"
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    try:
        status_text.text("🔌 Подключение к серверу Al-Style...")
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024 * 8
        downloaded = 0
        
        with open('temp_alstyle.xml', 'wb') as f:
            for data in response.iter_content(block_size):
                f.write(data)
                downloaded += len(data)
                if total_size > 0:
                    progress_bar.progress(min(downloaded / total_size, 1.0))
                status_text.text(f"📥 Скачивание прайса: {downloaded / (1024*1024):.2f} MB")
                
        status_text.text("✅ Прайс скачан. Начинаю чтение (парсинг) файла...")
        progress_bar.empty()
        
        # Parse the saved XML file
        tree = ET.parse('temp_alstyle.xml')
        root = tree.getroot()
        items = []
        
        try:
            with open("stop_brands.txt", "r", encoding="utf-8") as f:
                stop_brands = {line.strip().lower() for line in f if line.strip()}
        except FileNotFoundError:
            stop_brands = set()

        offers = root.findall('.//offer')
        total_offers = len(offers)
        
        for i, offer in enumerate(offers):
            if i % 500 == 0:
                status_text.text(f"⚙️ Обработка товаров: {i} из {total_offers}...")
            offer_id = offer.get('id', '')
            name = offer.findtext('name', 'Без названия')
            vendor = offer.findtext('vendor', 'Неизвестно')
            
            normalized_vendor = vendor.strip().lower()
            if normalized_vendor in stop_brands:
                continue
                
            price_elem = offer.findtext('purchase_price')
            if price_elem is None or not price_elem.strip():
                price_elem = offer.findtext('price', '0')
            try:
                purchase_price = float(price_elem)
            except ValueError:
                purchase_price = 0.0
            
            weight = 0.0
            for param in offer.findall('param'):
                if param.get('name') == 'Вес':
                    weight_text = param.text
                    if weight_text:
                        weight_text = weight_text.replace(',', '.')
                        just_digits_dot = ''.join(c for c in weight_text if c.isdigit() or c == '.')
                        try:
                            weight = float(just_digits_dot)
                        except ValueError:
                            weight = 0.0
                    break
            
            price = offer.findtext('price', '0')
            
            # Ищем остаток (может называться quantity, stock или instock)
            stock = offer.findtext('quantity', '0') 
            
            try:
                stock_int = int(float(stock))
            except ValueError:
                stock_int = 0
                
            # Фильтр: берем только те, где остаток 2 и больше
            if stock_int >= 2:
                items.append({
                    'Артикул поставщика': offer_id,
                    'Наименование': name,
                    'Бренд': vendor,
                    'Цена закупа': purchase_price,
                    'Остаток': stock_int,
                    'Вес (кг)': weight,
                    'Артикул Каспи': '',           # Пустое поле для твоего ввода
                    'Название Каспи': '',
                    'Цена на Каспи': 0.0,          # Будет заполняться парсером
                    'Минимальная цена': 0.0,       
                    'Цена реализации': 0.0,
                    'Предзаказ': 1
                })
                
        status_text.empty()
        return pd.DataFrame(items)
        
    except Exception as e:
        st.error(f"Ошибка при скачивании или парсинге XML: {e}")
        return pd.DataFrame()

def calculate_price_for_profit(supplier_price, weight=0, target_profit=0):
    if not supplier_price or pd.isna(supplier_price):
        return 0
        
    COEFF = 0.845  # 1 - (12.5% Kaspi Fee + 3% Tax)
    VAT = 1.16     # 16% VAT added to base delivery cost
    
    def calc_price(delivery_base):
        delivery_with_vat = delivery_base * VAT
        return (supplier_price + delivery_with_vat + target_profit) / COEFF

    # Check price-based tiers (< 10,000 KZT)
    price = calc_price(49.14)
    if price <= 999: return round(price)
    
    price = calc_price(99.14)
    if price <= 2000: return round(price)
    
    price = calc_price(199.14)
    if price <= 3000: return round(price)
    
    price = calc_price(399.14)
    if price <= 5000: return round(price)
    
    price = calc_price(799.14)
    if price <= 10000: return round(price)
    
    # If selling price is > 10,000 KZT, delivery is weight-based
    try:
        w = float(weight) if pd.notna(weight) and weight else 0.0
    except ValueError:
        w = 0.0
        
    if w <= 5:
        delivery_base = 1299.14
    elif w <= 10:
        delivery_base = 1699.14
    elif w <= 15:
        delivery_base = 2199.14
    elif w <= 20:
        delivery_base = 2599.14
    elif w <= 30:
        delivery_base = 3499.14
    else:
        delivery_base = 5000.0 # Fallback for very heavy items
        
    return round(calc_price(delivery_base))

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
                    
                    if preorder_days > 3:
                        # Агрессивный демпинг: -20% от цены конкурента для товаров из Китая
                        calculated_price = k_price_val * 0.8
                        f_price_val = max(m_price_val, calculated_price)
                    else:
                        # Стандартный демпинг: -5 тенге для товаров в наличии (1-3 дня)
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
                
            try:
                supabase_client.table('products').update(update_data).eq("kaspi_sku", sku).execute()
            except Exception as e:
                print(f"Error updating database for {sku}: {e}")
                
        await browser.close()
            
    return prices_dict

def generate_kaspi_xml(df: pd.DataFrame, merchant_id="30391602", city_id="750000000") -> bytes:
    from datetime import datetime
    import xml.etree.ElementTree as ET
    
    filtered_df = df[df['Артикул Каспи'].astype(str).str.strip() != '']
    filtered_df = filtered_df[filtered_df['Артикул Каспи'].astype(str).str.lower() != 'nan']
    
    # Выгружаем только ручные товары или те, у которых is_approved == True
    is_approved_mask = filtered_df.get('Одобрен', pd.Series([False]*len(filtered_df))) == True
    is_manual_mask = filtered_df['Артикул поставщика'].astype(str).str.lower().str.startswith('m-')
    filtered_df = filtered_df[is_approved_mask | is_manual_mask]
    
    # Create root element with exact namespaces
    root = ET.Element("kaspi_catalog", {
        "xmlns": "kaspiShopping",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": "http://kaspi.kz/kaspishopping.xsd",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    
    ET.SubElement(root, "company").text = merchant_id
    ET.SubElement(root, "merchantid").text = merchant_id
    offers = ET.SubElement(root, "offers")
    
    for _, row in filtered_df.iterrows():
        sku = str(row['Артикул Каспи']).strip()
        offer = ET.SubElement(offers, "offer", sku=sku)
        
        # Model (prefer Kaspi name, fallback to Supplier name)
        model = str(row['Название Каспи']).strip()
        if not model or model.lower() == 'nan':
            model = str(row['Наименование']).strip()
        ET.SubElement(offer, "model").text = model
        
        # Brand
        brand = str(row['Бренд']).strip()
        if brand and brand.lower() != 'nan':
            ET.SubElement(offer, "brand").text = brand
            
        # Availabilities
        availabilities = ET.SubElement(offer, "availabilities")
        try:
            stock = float(row['Остаток'])
        except (ValueError, TypeError):
            stock = 0.0
            
        available_str = "yes" if stock > 0 else "no"
        store_id_str = f"{merchant_id}_PP1"
        
        ET.SubElement(availabilities, "availability", 
                      available=available_str, 
                      storeId=store_id_str, 
                      preOrder="0", 
                      stockCount=f"{stock:.1f}")
            
        # City Prices
        price_val = 0
        try:
            price_val = int(float(row['Цена реализации']))
        except (ValueError, TypeError):
            pass
            
        cityprices = ET.SubElement(offer, "cityprices")
        ET.SubElement(cityprices, "cityprice", cityId=city_id).text = str(price_val)
        
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)

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
                            "is_approved": True
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
    MERCHANT_ID = "30391602" # Указан ID из примера
    
    xml_data = generate_kaspi_xml(df, merchant_id=MERCHANT_ID)
    
    col_dl, col_pub = st.columns([1, 2])
    with col_dl:
        st.download_button(
            label="📥 Скачать XML для Kaspi",
            data=xml_data,
            file_name="kaspi_prices.xml",
            mime="application/xml",
            type="primary"
        )
        
    with col_pub:
        if st.button("🌐 Опубликовать XML по ссылке"):
            with st.spinner("Загрузка в облако Supabase..."):
                try:
                    res = supabase_client.storage.from_("kaspi").upload(
                        path="kaspi_prices.xml", 
                        file=xml_data, 
                        file_options={"upsert": "true", "contentType": "application/xml"}
                    )
                    public_url = supabase_client.storage.from_("kaspi").get_public_url("kaspi_prices.xml")
                    st.success("✅ XML файл успешно опубликован!")
                    st.info("Скопируй эту ссылку и вставь в настройки автоматического обновления Каспи:")
                    st.code(public_url)
                except Exception as e:
                    st.error(f"❌ Ошибка публикации. Подробности: {e}")

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

    # Пагинация и сортировка (пустые Артикулы Каспи наверх)
    mask = display_df['Артикул Каспи'].notna() & \
           (display_df['Артикул Каспи'].astype(str).str.strip() != '') & \
           (display_df['Артикул Каспи'].astype(str).str.lower() != 'none') & \
           (display_df['Артикул Каспи'].astype(str).str.lower() != 'nan')
           
    display_df = pd.concat([display_df[~mask], display_df[mask]]).reset_index(drop=True)

    page_size = 50
    total_pages = max(1, len(display_df) // page_size + (1 if len(display_df) % page_size > 0 else 0))
    page_number = st.number_input("Страница", min_value=1, max_value=total_pages, value=1)
    
    start_idx = (page_number - 1) * page_size
    end_idx = start_idx + page_size
    current_page = display_df.iloc[start_idx:end_idx].copy()
    current_page.reset_index(drop=True, inplace=True)
    
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
                sku_details[sku] = {
                    'purchase_price': float(row['Цена закупа']) if pd.notna(row['Цена закупа']) else 0.0,
                    'weight': float(row['Вес (кг)']) if pd.notna(row['Вес (кг)']) else 0.0,
                    'preorder': int(row['Предзаказ']) if pd.notna(row['Предзаказ']) else 1
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
            
            status_text.text("Парсинг завершен!")
            st.success("Цены обновлены.")
            st.rerun()

else:
    st.warning("Нет данных для отображения. Проверь структуру XML-файла.")