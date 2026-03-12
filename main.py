import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET

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
        
        # Обычно в YML товары лежат в тегах <offer>
        for offer in root.findall('.//offer'):
            offer_id = offer.get('id', '')
            name = offer.findtext('name', 'Без названия')
            vendor = offer.findtext('vendor', 'Неизвестно')
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
else:
    st.warning("Нет данных для отображения. Проверь структуру XML-файла.")