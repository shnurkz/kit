import sqlite3
import pandas as pd
from supabase import create_client, Client

# Настройки Supabase
SUPABASE_URL = "https://akrygxdwrwyoaxdsjefs.supabase.co"
SUPABASE_KEY = "sb_publishable_iaXWAlU-358SXmtzzhDIag_33TiVNj-"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

print("Подключаемся к старой базе mapping.db...")
conn = sqlite3.connect('mapping.db')

# Вытягиваем только те товары, где ты уже заполнил kaspi_sku
df = pd.read_sql("SELECT supplier_sku, kaspi_sku, kaspi_name, kaspi_price, min_price, final_price FROM products WHERE kaspi_sku IS NOT NULL AND kaspi_sku != ''", conn)
conn.close()

print(f"Найдено {len(df)} товаров с привязанными артикулами Каспи. Начинаю перенос...")

# Отправляем обновления в Supabase
success_count = 0
for index, row in df.iterrows():
    try:
        update_data = {
            "kaspi_sku": str(row['kaspi_sku']),
            "kaspi_name": str(row['kaspi_name']) if pd.notna(row['kaspi_name']) else "",
            "kaspi_price": float(row['kaspi_price']) if pd.notna(row['kaspi_price']) else 0,
            "min_price": float(row['min_price']) if pd.notna(row['min_price']) else 0,
            "final_price": float(row['final_price']) if pd.notna(row['final_price']) else 0
        }
        # Обновляем строку по артикулу поставщика
        supabase.table('products').update(update_data).eq('supplier_sku', str(row['supplier_sku'])).execute()
        success_count += 1
        print(f"[{success_count}/{len(df)}] Перенесен: {row['supplier_sku']} -> {row['kaspi_sku']}")
    except Exception as e:
        print(f"Ошибка с товаром {row['supplier_sku']}: {e}")

print("Миграция успешно завершена!")