import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright
import time
import traceback
import sys
import asyncio
import sqlite3

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
        db_df = pd.read_sql_query("SELECT * FROM products", conn)
        
    db_df = db_df.rename(columns={
        'supplier_sku': 'Артикул поставщика',
        'name': 'Наименование',
        'brand': 'Бренд',
        'supplier_price': 'Цена закупа',
        'stock': 'Остаток',
        'kaspi_sku': 'Артикул Каспи',
        'kaspi_price': 'Цена на Каспи',
        'weight': 'Вес (кг)',
        'min_price': 'Минимальная цена',
        'final_price': 'Цена реализации'
    })
    return db_df


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

def fetch_batch_kaspi_prices(sku_list: list, progress_bar, status_text) -> dict:
    prices_dict = {}
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            try:
                for i, sku in enumerate(sku_list):
                    status_text.text(f"Парсинг артикула {sku} ({i+1}/{len(sku_list)})...")
                    progress_bar.progress((i + 1) / len(sku_list))
                    
                    prices_found = []
                    try:
                        page.goto(f"https://kaspi.kz/shop/search/?text={sku}")
                        
                        page.wait_for_selector('a[href*="/p/"]', timeout=15000)
                        product_href = page.locator('a[href*="/p/"]').first.get_attribute("href")
                        
                        if product_href:
                            product_url = f"https://kaspi.kz{product_href}" if product_href.startswith("/") else product_href
                            page.goto(product_url)
                            page.wait_for_timeout(1500)
                            
                            tab_locator = page.locator('text="Продавцы"').first
                            tab_locator.wait_for(timeout=15000)
                            tab_locator.evaluate("element => element.click()")
                            page.wait_for_timeout(2000)
                        
                        page.wait_for_selector("table tbody tr", timeout=15000)
                        rows = page.locator("table tbody tr").all()
                        
                        for row in rows:
                            cells = row.locator("td").all()
                            if len(cells) < 4:
                                continue
                                
                            seller_name = cells[0].inner_text()
                            if "ИП EVENTRENT" in seller_name:
                                continue
                                
                            price_text = cells[3].inner_text()
                            price_part = price_text.split('₸')[0]
                            just_digits = ''.join(c for c in price_part if c.isdigit())
                            
                            if just_digits:
                                prices_found.append(float(just_digits))
                                
                        if prices_found:
                            min_price = min(prices_found)
                            prices_dict[sku] = min_price
                        else:
                            prices_dict[sku] = 0.0
                            
                        with sqlite3.connect("mapping.db") as conn:
                            cursor = conn.cursor()
                            cursor.execute("SELECT supplier_price, weight FROM products WHERE kaspi_sku = ?", (sku,))
                            row_db = cursor.fetchone()
                            if row_db:
                                purchase_price, weight = row_db
                                min_price, final_price = calculate_target_prices(purchase_price, weight, prices_dict[sku])
                                cursor.execute("UPDATE products SET kaspi_price = ?, min_price = ?, final_price = ? WHERE kaspi_sku = ?", (prices_dict[sku], min_price, final_price, sku))
                            else:
                                cursor.execute("UPDATE products SET kaspi_price = ? WHERE kaspi_sku = ?", (prices_dict[sku], sku))
                            conn.commit()
                            
                    except Exception as e:
                        print(f"Error fetching Kaspi price for {sku}: {traceback.format_exc()}")
                        prices_dict[sku] = 0.0
                        
                    time.sleep(3)
            finally:
                browser.close()
            
    except Exception as e:
        st.error(f"Scraper Error:\\n{traceback.format_exc()}")
        
    return prices_dict

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

    # Выводим интерактивную таблицу
    edited_df = st.data_editor(
        df, 
        use_container_width=True,
        hide_index=True,
        disabled=["Артикул поставщика", "Наименование", "Бренд", "Цена закупа", "Остаток", "Вес (кг)", "Минимальная цена", "Цена реализации"],
        key="data_editor"
    )
    
    # Мгновенное сохранение ручных изменений артикулов в базу данных
    for index, row in edited_df.iterrows():
        old_kaspi_sku = str(df.at[index, 'Артикул Каспи']).strip()
        new_kaspi_sku = str(row['Артикул Каспи']).strip()
        
        if old_kaspi_sku == 'nan': old_kaspi_sku = ''
        if new_kaspi_sku == 'nan': new_kaspi_sku = ''
        
        if old_kaspi_sku != new_kaspi_sku:
            supplier_sku = row['Артикул поставщика']
            with sqlite3.connect("mapping.db") as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE products SET kaspi_sku = ? WHERE supplier_sku = ?", (new_kaspi_sku, supplier_sku))
                conn.commit()
            st.session_state.df.at[index, 'Артикул Каспи'] = new_kaspi_sku

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
            
            # Запускаем парсер
            prices_dict = fetch_batch_kaspi_prices(skus_to_fetch, progress_bar, status_text)
            
            # Перезагружаем из БД, чтобы обновить UI
            st.session_state.df = load_data_from_db()
            
            status_text.text("Парсинг завершен!")
            st.success("Цены обновлены.")
            st.rerun()

else:
    st.warning("Нет данных для отображения. Проверь структуру XML-файла.")