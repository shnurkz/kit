import os
import time
import logging
import requests
import pandas as pd
import xml.etree.ElementTree as ET
from supabase import create_client, Client, ClientOptions

import sys

# Force UTF-8 encoding for standard output and error to avoid UnicodeEncodeError on Windows terminals
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("core_updater")

SUPABASE_URL = "https://akrygxdwrwyoaxdsjefs.supabase.co"
SUPABASE_KEY = "sb_secret_-6yiRL3AwNCJ3EHqtPW-ww_oiVqN6f_"
opts = ClientOptions(postgrest_client_timeout=15)
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=opts)

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

def load_and_parse_xml(
    url="https://apifeed.al-style.kz/feed.xml",
    progress_callback=None,
    status_callback=None,
    error_callback=None
):
    try:
        msg = "🔌 Подключение к серверу Al-Style..."
        logger.info(msg)
        if status_callback:
            status_callback(msg)
            
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
                    pct = min(downloaded / total_size, 1.0)
                    if progress_callback:
                        progress_callback(pct)
                msg = f"📥 Скачивание прайса: {downloaded / (1024*1024):.2f} MB"
                logger.info(msg)
                if status_callback:
                    status_callback(msg)
                    
        msg = "✅ Прайс скачан. Начинаю чтение (парсинг) файла..."
        logger.info(msg)
        if status_callback:
            status_callback(msg)
            
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
                msg = f"⚙️ Обработка товаров: {i} из {total_offers}..."
                logger.info(msg)
                if status_callback:
                    status_callback(msg)
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
                    'Артикул Каспи': '',
                    'Название Каспи': '',
                    'Цена на Каспи': 0.0,
                    'Минимальная цена': 0.0,       
                    'Цена реализации': 0.0,
                    'Предзаказ': 1
                })
                
        return pd.DataFrame(items)
        
    except Exception as e:
        err_msg = f"Ошибка при скачивании или парсинге XML: {e}"
        logger.error(err_msg)
        if error_callback:
            error_callback(err_msg)
        return pd.DataFrame()

def sync_supplier_to_db(
    xml_df,
    db_skus=None,
    progress_callback=None,
    status_callback=None,
    error_callback=None
):
    msg = f"☁️ Начинаю отправку {len(xml_df)} товаров в базу..."
    logger.info(msg)
    if status_callback:
        status_callback(msg)
        
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
                if progress_callback:
                    progress_callback(min(uploaded_count / total_products, 1.0))
                msg = f"☁️ Отправлено в облако: {uploaded_count} из {total_products}..."
                logger.info(msg)
                if status_callback:
                    status_callback(msg)
                batch = []
            except Exception as e:
                err_msg = f"❌ Ошибка сети при отправке данных: {e}"
                logger.error(err_msg)
                if error_callback:
                    error_callback(err_msg)
                break
                
    if batch:
        try:
            supabase_client.table('products').upsert(batch).execute()
            uploaded_count += len(batch)
            if progress_callback:
                progress_callback(1.0)
            msg = f"☁️ Отправлено в облако: {uploaded_count} из {total_products}..."
            logger.info(msg)
            if status_callback:
                status_callback(msg)
        except Exception as e:
            err_msg = f"❌ Ошибка при отправке последней пачки: {e}"
            logger.error(err_msg)
            if error_callback:
                error_callback(err_msg)

    # 2. Find Missing SKUs
    if db_skus is None:
        db_skus = set()
        start = 0
        limit = 1000
        while True:
            try:
                response = supabase_client.table('products').select('supplier_sku').range(start, start + limit - 1).execute()
                data = response.data
                if not data:
                    break
                for d in data:
                    db_skus.add(str(d.get('supplier_sku', '')))
                if len(data) < limit:
                    break
                start += limit
            except Exception as e:
                logger.error(f"Error fetching DB SKUs: {e}")
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
        msg = f"🧹 Обнуление остатков для {len(missing_skus)} снятых с продажи товаров..."
        logger.info(msg)
        if status_callback:
            status_callback(msg)
            
        chunk_size = 200
        for i in range(0, len(missing_skus), chunk_size):
            chunk = missing_skus[i:i + chunk_size]
            try:
                supabase_client.table('products').update({"stock": 0}).in_("supplier_sku", chunk).execute()
            except Exception as e:
                logger.error(f"Error zeroing stock for chunk: {e}")
