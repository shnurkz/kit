import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright
import time
import traceback
import sys
import asyncio
import sqlite3
import io

def init_db():
    with sqlite3.connect("mapping.db") as conn:
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            supplier_sku TEXT PRIMARY KEY,
            name TEXT,
            brand TEXT,
            supplier_price REAL,
            stock INTEGER,
            kaspi_sku TEXT,
            kaspi_price REAL
        )
        ''')
        
        cursor.execute("PRAGMA table_info(products)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'weight' not in columns:
            cursor.execute("ALTER TABLE products ADD COLUMN weight REAL DEFAULT 0.0")
        if 'min_price' not in columns:
            cursor.execute("ALTER TABLE products ADD COLUMN min_price REAL DEFAULT 0.0")
        if 'final_price' not in columns:
            cursor.execute("ALTER TABLE products ADD COLUMN final_price REAL DEFAULT 0.0")
            
        conn.commit()

init_db()

def sync_supplier_to_db(xml_df):
    with sqlite3.connect("mapping.db") as conn:
        cursor = conn.cursor()
        for _, row in xml_df.iterrows():
            cursor.execute('''
            INSERT INTO products (supplier_sku, name, brand, supplier_price, stock, kaspi_sku, kaspi_price, weight, min_price, final_price)
            VALUES (?, ?, ?, ?, ?, '', 0.0, ?, 0.0, 0.0)
            ON CONFLICT(supplier_sku) DO UPDATE SET
                name = excluded.name,
                brand = excluded.brand,
                supplier_price = excluded.supplier_price,
                stock = excluded.stock,
                weight = excluded.weight
            ''', (
                row['Артикул поставщика'],
                row['Наименование'],
                row['Бренд'],
                row['Цена закупа'],
                row['Остаток'],
                row['Вес (кг)']
            ))
        conn.commit()

def load_data_from_db():
    with sqlite3.connect("mapping.db") as conn:
        db_df = pd.read_sql_query('''
            SELECT supplier_sku, name as supplier_name, brand, supplier_price, stock, weight, kaspi_sku, name as kaspi_name, kaspi_price, min_price, final_price 
            FROM products
        ''', conn)
        
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
        'final_price': 'Цена реализации'
    })
    return db_df

def save_table_edits():
    if "product_editor" in st.session_state:
        edited_rows = st.session_state["product_editor"].get("edited_rows", {})
        for row_idx, changes in edited_rows.items():
            if 'current_page_df' in st.session_state and row_idx < len(st.session_state.current_page_df):
                actual_index = st.session_state.current_page_df.index[row_idx]
                supplier_sku = st.session_state.df.at[actual_index, 'Артикул поставщика']
                
                if 'Артикул Каспи' in changes:
                    new_sku = str(changes['Артикул Каспи']).strip()
                    if new_sku == 'nan': new_sku = ''
                    
                    with sqlite3.connect('mapping.db') as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET kaspi_sku = ? WHERE supplier_sku = ?", (new_sku, supplier_sku))
                        conn.commit()
                    
                    st.session_state.df.at[actual_index, 'Артикул Каспи'] = new_sku

# Настройка страницы
st.set_page_config(page_title="Kaspi Manager", layout="wide")
st.title("Управление товарами Kaspi")

@st.cache_data(ttl=600) # Кэшируем данные на 10 минут, чтобы не качать XML при каждом клике
def load_and_parse_xml():
    url = "https://apifeed.al-style.kz/feed.xml"
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        # Парсим XML
        root = ET.fromstring(response.content)
        items = []
        
        try:
            with open("stop_brands.txt", "r", encoding="utf-8") as f:
                stop_brands = {line.strip().lower() for line in f if line.strip()}
        except FileNotFoundError:
            stop_brands = set()

        # Обычно в YML товары лежат в тегах <offer>
        for offer in root.findall('.//offer'):
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
                    'Цена реализации': 0.0         
                })
                
        return pd.DataFrame(items)
        
    except Exception as e:
        st.error(f"Ошибка при скачивании или парсинге XML: {e}")
        return pd.DataFrame()

def calculate_target_prices(purchase_price: float, weight: float, kaspi_competitor_price: float) -> tuple[float, float]:
    estimated_tier_price = purchase_price / 0.845
    
    if estimated_tier_price < 10000:
        if weight <= 1.0:
            shipping_cost = 49.14
        elif weight <= 3.0:
            shipping_cost = 149.14
        elif weight <= 5.0:
            shipping_cost = 199.14
        else:
            shipping_cost = 799.14
    else:
        if weight < 5.0:
            shipping_cost = 1299.14
        elif weight <= 15.0:
            shipping_cost = 1699.14
        elif weight <= 30.0:
            shipping_cost = 3599.14
        elif weight <= 60.0:
            shipping_cost = 5649.14
        elif weight <= 100.0:
            shipping_cost = 8549.14
        else:
            shipping_cost = 11999.14
            
    shipping_with_vat = shipping_cost * 1.16
    min_price = (purchase_price + shipping_with_vat) / 0.845
    
    if kaspi_competitor_price > 0:
        if kaspi_competitor_price > min_price:
            final_price = kaspi_competitor_price - 5
        else:
            final_price = min_price
    else:
        final_price = min_price
        
    return min_price, final_price

async def fetch_batch_kaspi_prices_async(sku_list: list, progress_bar, status_text) -> dict:
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
                                # Try the user's specific XPath first
                                name_locator = page.locator('xpath=/html/body/div[1]/div[3]/div/div[2]/div/div[2]/div/div[1]/h1').first
                                await name_locator.wait_for(state="attached", timeout=3000)
                                kaspi_name = await name_locator.inner_text()
                            except Exception:
                                try:
                                    # Fallback to generic h1 with product class
                                    name_locator = page.locator('h1.item__heading').first
                                    await name_locator.wait_for(state="attached", timeout=2000)
                                    kaspi_name = await name_locator.inner_text()
                                except Exception as e:
                                    print(f"Name extraction failed for {sku}: {traceback.format_exc()}")
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
        
        for res in results:
            if not res:
                continue
            sku, min_price, kaspi_name = res
            
            prices_dict[sku] = {
                'min_price': min_price,
                'kaspi_name': kaspi_name
            }
            
            with sqlite3.connect("mapping.db") as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT supplier_price, weight FROM products WHERE kaspi_sku =?", (sku,))
                row_db = cursor.fetchone()
                
                if row_db:
                    purchase_price, weight = row_db
                    calc_min_price, final_price = calculate_target_prices(purchase_price, weight, min_price)
                    cursor.execute("UPDATE products SET kaspi_price = ?, min_price = ?, final_price = ?, name = CASE WHEN ? != '' THEN ? ELSE name END WHERE kaspi_sku = ?", 
                                   (min_price, calc_min_price, final_price, kaspi_name, kaspi_name, sku))
                else:
                    cursor.execute("UPDATE products SET kaspi_price = ?, name = CASE WHEN ? != '' THEN ? ELSE name END WHERE kaspi_sku = ?", 
                                   (min_price, kaspi_name, kaspi_name, sku))
                conn.commit()
                
        await browser.close()
            
    return prices_dict

def generate_kaspi_excel(df: pd.DataFrame) -> bytes:
    filtered_df = df[df['Артикул Каспи'].astype(str).str.strip() != '']
    filtered_df = filtered_df[filtered_df['Артикул Каспи'].astype(str).str.strip() != 'nan']
    
    kaspi_df = pd.DataFrame()
    kaspi_df['SKU'] = filtered_df['Артикул Каспи']
    kaspi_df['model'] = filtered_df['Наименование']
    kaspi_df['brand'] = filtered_df['Бренд']
    kaspi_df['price'] = filtered_df['Цена реализации'].fillna(0).astype(int)
    kaspi_df['PP1'] = filtered_df['Остаток'].fillna(0).astype(int)
    kaspi_df['PP2'] = 'no'
    kaspi_df['PP3'] = 'no'
    kaspi_df['PP4'] = 'no'
    kaspi_df['PP5'] = 'no'
    kaspi_df['preorder'] = 0
    
    output = io.BytesIO()
    kaspi_df.to_excel(output, index=False, engine='openpyxl')
    return output.getvalue()

# Загружаем данные
st.write("Скачивание и обработка прайса Al-Style...")

xml_df = load_and_parse_xml()
if not xml_df.empty:
    sync_supplier_to_db(xml_df)

if 'df' not in st.session_state:
    st.session_state.df = load_data_from_db()

df = st.session_state.df

if not df.empty:
    st.success(f"Успешно загружено товаров (с остатком >= 2): {len(df)}")
    
    with st.expander("📦 Добавить свой товар (ручной ввод)"):
        with st.form("custom_product_form"):
            col1, col2 = st.columns(2)
            with col1:
                custom_sku = st.text_input("Артикул / Код товара (обязательно)")
                custom_name = st.text_input("Наименование (обязательно)")
                custom_brand = st.text_input("Бренд")
            with col2:
                custom_price = st.number_input("Цена закупа", min_value=0.0, format="%.2f")
                custom_stock = st.number_input("Остаток на складе", min_value=0, step=1)
                custom_weight = st.number_input("Вес в кг", min_value=0.0, format="%.3f")
                custom_kaspi_sku = st.text_input("Артикул Каспи (необязательно)")
                
            submitted = st.form_submit_button("Сохранить в базу")
            
            if submitted:
                if custom_sku.strip() and custom_name.strip():
                    min_price, final_price = calculate_target_prices(custom_price, custom_weight, 0.0)
                    with sqlite3.connect("mapping.db") as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                        INSERT INTO products (supplier_sku, name, brand, supplier_price, stock, kaspi_sku, kaspi_price, weight, min_price, final_price)
                        VALUES (?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?)
                        ON CONFLICT(supplier_sku) DO UPDATE SET
                            name = excluded.name,
                            brand = excluded.brand,
                            supplier_price = excluded.supplier_price,
                            stock = excluded.stock,
                            kaspi_sku = case when excluded.kaspi_sku != '' then excluded.kaspi_sku else kaspi_sku end,
                            weight = excluded.weight,
                            min_price = excluded.min_price,
                            final_price = excluded.final_price
                        ''', (
                            custom_sku.strip(), 
                            custom_name.strip(), 
                            custom_brand.strip(), 
                            custom_price, 
                            custom_stock, 
                            custom_kaspi_sku.strip(),
                            custom_weight,
                            min_price,
                            final_price
                        ))
                        conn.commit()
                    st.success("Товар успешно добавлен!")
                    st.session_state.df = load_data_from_db()
                    st.rerun()
                else:
                    st.error("Артикул и Наименование обязательны для заполнения!")

    # Кнопка скачивания Excel для Kaspi
    excel_data = generate_kaspi_excel(df)
    st.download_button(
        label="📥 Скачать Excel для Kaspi",
        data=excel_data,
        file_name="kaspi_upload.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )

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

    # Пагинация
    page_size = 50
    total_pages = max(1, len(display_df) // page_size + (1 if len(display_df) % page_size > 0 else 0))
    page_number = st.number_input("Страница", min_value=1, max_value=total_pages, value=1)
    
    start_idx = (page_number - 1) * page_size
    end_idx = start_idx + page_size
    st.session_state.current_page_df = display_df.iloc[start_idx:end_idx]

    # Выводим интерактивную таблицу
    st.data_editor(
        st.session_state.current_page_df, 
        column_order=["Артикул поставщика", "Наименование", "Бренд", "Цена закупа", "Остаток", "Вес (кг)", "Артикул Каспи", "Название Каспи", "Цена на Каспи", "Минимальная цена", "Цена реализации"],
        use_container_width=True,
        hide_index=True,
        disabled=["Артикул поставщика", "Наименование", "Бренд", "Цена закупа", "Остаток", "Вес (кг)", "Название Каспи", "Цена на Каспи", "Минимальная цена", "Цена реализации"],
        key="product_editor",
        on_change=save_table_edits
    )
            
    # Кнопка для запуска парсера
    if st.button("Запросить цены Каспи"):
        st.write("Запускаем сбор цен...")
        
        # Находим строки, где заполнен артикул Каспи
        valid_rows = st.session_state.df[st.session_state.df['Артикул Каспи'].astype(str).str.strip() != '']
        valid_rows = valid_rows[valid_rows['Артикул Каспи'].astype(str).str.strip() != 'nan']
        
        if valid_rows.empty:
            st.warning("Нет заполненных артикулов Каспи для парсинга.")
        else:
            skus_to_fetch = valid_rows['Артикул Каспи'].astype(str).str.strip().tolist()
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Запускаем парсер асинхронно
            if sys.platform == 'win32':
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            prices_dict = loop.run_until_complete(fetch_batch_kaspi_prices_async(skus_to_fetch, progress_bar, status_text))
            
            # Перезагружаем из БД, чтобы обновить UI
            st.session_state.df = load_data_from_db()
            
            status_text.text("Парсинг завершен!")
            st.success("Цены обновлены.")
            st.rerun()

else:
    st.warning("Нет данных для отображения. Проверь структуру XML-файла.")