import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright
import time
import traceback
import sys
import asyncio

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
                    'Цена закупа': float(price),
                    'Остаток': stock_int,
                    'Артикул Каспи': '',           # Пустое поле для твоего ввода
                    'Цена на Каспи': 0.0,          # Будет заполняться парсером
                    'Окончательная цена': 0.0      # Будет считаться по формуле
                })
                
        return pd.DataFrame(items)
        
    except Exception as e:
        st.error(f"Ошибка при скачивании или парсинге XML: {e}")
        return pd.DataFrame()

def fetch_single_kaspi_price(sku: str) -> float:
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            
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
            finally:
                time.sleep(3)
                browser.close()
                
            if prices_found:
                return min(prices_found)
            return 0.0
            
    except Exception as e:
        st.error(f"Scraper Error:\\n{traceback.format_exc()}")
        return 0.0

# Загружаем данные
st.write("Скачивание и обработка прайса Al-Style...")
df = load_and_parse_xml()

if not df.empty:
    st.success(f"Успешно загружено товаров (с остатком >= 2): {len(df)}")
    
    # Выводим интерактивную таблицу
    edited_df = st.data_editor(
        df, 
        use_container_width=True,
        hide_index=True,
        disabled=["Артикул поставщика", "Наименование", "Бренд", "Цена закупа", "Остаток"] # Эти поля нельзя менять руками
    )
    
    if st.button("Сохранить артикулы в базу"):
        st.info("Здесь позже прикрутим сохранение в SQLite")
        
    st.divider()
    st.subheader("Тестирование парсера Каспи")
    test_sku = st.text_input("Введите артикул (SKU) для поиска на Kaspi", "")
    if st.button("Тест парсера Каспи"):
        if test_sku:
            with st.spinner(f"Ищем цену для {test_sku}... Откроется браузер."):
                price = fetch_single_kaspi_price(test_sku)
            st.success(f"Минимальная цена (без ИП EVENTRENT): {price} ₸")
        else:
            st.warning("Введите артикул.")
else:
    st.warning("Нет данных для отображения. Проверь структуру XML-файла.")