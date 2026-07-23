import os
import sys
import time
import logging
import traceback
from datetime import datetime

# Force UTF-8 encoding for standard output and error on Windows terminal
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

# Setup logging configuration
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# File Handler
file_handler = logging.FileHandler('updater_log.txt', encoding='utf-8')
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# Root Logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

logger = logging.getLogger("loader")

import core_updater

def main():
    print("=" * 60)
    print("      ЗАГРУЗЧИК ПРАЙСА ПОСТАВЩИКА И ОБНОВЛЕНИЕ БД      ")
    print("=" * 60)
    print(f"Старт: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)
    
    start_time = time.time()
    
    def status_cb(msg):
        logger.info(f"[СТАТУС] {msg}")
        
    def progress_cb(pct):
        pct_val = int(pct * 100)
        # Log progress every 10%
        if pct_val % 10 == 0:
            logger.info(f"[ПРОГРЕСС] Выполнено: {pct_val}%")

    def error_cb(err):
        logger.error(f"[ОШИБКА] {err}")

    try:
        # Шаг 1: Загрузка и парсинг XML от поставщика Al-Style
        logger.info(">>> ШАГ 1: Запрос и скачивание обновленного прайса от поставщика Al-Style...")
        df_xml = core_updater.load_and_parse_xml(
            progress_callback=progress_cb,
            status_callback=status_cb,
            error_callback=error_cb
        )
        
        if df_xml.empty:
            logger.error("❌ Не удалось получить данные из прайса (DataFrame пуст). Обновление отменено.")
            return
            
        total_parsed = len(df_xml)
        logger.info(f"✅ Успешно распаршено {total_parsed} товаров со склада поставщика.")
        print("-" * 60)
        
        # Шаг 2: Синхронизация товаров с БД Supabase
        logger.info(">>> ШАГ 2: Синхронизация данных с облачной баз данных (Supabase)...")
        core_updater.sync_supplier_to_db(
            xml_df=df_xml,
            db_skus=None,
            progress_callback=progress_cb,
            status_callback=status_cb,
            error_callback=error_cb
        )
        
        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"🎉 ОБНОВЛЕНИЕ БАЗЫ ДАННЫХ УСПЕШНО ЗАВЕРШЕНО!")
        logger.info(f"⏱️ Общее время выполнения: {elapsed:.2f} сек ({elapsed/60:.2f} мин)")
        logger.info(f"📦 Всего обработано актуальных товаров: {total_parsed}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка во время выполнения загрузчика: {e}")
        logger.critical(traceback.format_exc())

if __name__ == "__main__":
    main()
    print("\n")
    if sys.stdin and sys.stdin.isatty():
        input("Нажмите Enter для завершения работы...")

