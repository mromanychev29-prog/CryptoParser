import websocket
import json
import threading
import requests
from typing import Dict, List, Set
import time
import math
import logging
import telebot
from telebot.types import Message

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BybitDataCollector:
    def __init__(self):
        self.current_data = {}
        self.ws_connections = []
        self.is_connected = False
        self.lock = threading.Lock()
        self.available_tickers = set()
        self.subscribed_tickers = set()
        self.total_batches = 0
        self.completed_batches = 0
        self.user_requests = {}  # Для логирования запросов пользователей
        
    def fetch_spot_tickers(self) -> List[str]:
        """Получаем список всех спотовых тикетов с Bybit"""
        try:
            url = "https://api.bybit.com/v5/market/instruments-info"
            params = {"category": "spot"}
            response = requests.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                tickers = [item['symbol'] for item in data['result']['list']]
                self.available_tickers = set(tickers)
                print(f"✅ Получено {len(tickers)} тикетов")
                logger.info(f"Получено {len(tickers)} тикетов с Bybit")
                return tickers
            else:
                print(f"❌ Ошибка получения тикетов: {response.status_code}")
                logger.error(f"Ошибка получения тикетов: {response.status_code}")
                return []
                
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            logger.error(f"Ошибка при получении тикетов: {e}")
            return []
    
    def create_batches(self, tickers: List[str], batch_size: int = 10) -> List[List[str]]:
        """Создает батчи тикетов для подписки"""
        batches = []
        for i in range(0, len(tickers), batch_size):
            batches.append(tickers[i:i + batch_size])
        return batches
    
    def start_websocket_for_batch(self, batch: List[str], batch_id: int):
        """Запускает WebSocket подключение для одного батча тикетов"""
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                
                # Пропускаем сообщения о подписке
                if 'success' in data and data['success']:
                    return
                
                # Обрабатываем данные тикера
                if 'topic' in data and 'tickers' in data['topic']:
                    ticker_data = data['data']
                    if isinstance(ticker_data, dict):
                        symbol = ticker_data.get('symbol')
                        if symbol:
                            with self.lock:
                                self.current_data[symbol] = ticker_data
                    elif isinstance(ticker_data, list):
                        for item in ticker_data:
                            symbol = item.get('symbol')
                            if symbol:
                                with self.lock:
                                    self.current_data[symbol] = item
                            
            except Exception as e:
                # Тихий режим - не выводим ошибки
                pass
        
        def on_error(ws, error):
            # Тихий режим - не выводим ошибки
            pass
        
        def on_close(ws, close_status_code, close_msg):
            # Убираем соединение из списка
            with self.lock:
                if ws in self.ws_connections:
                    self.ws_connections.remove(ws)
        
        def on_open(ws):
            # Подписываемся на тикеры в этом батче
            subscribe_message = {
                "op": "subscribe",
                "args": [f"tickers.{ticker}" for ticker in batch]
            }
            
            ws.send(json.dumps(subscribe_message))
            self.subscribed_tickers.update(batch)
            
            with self.lock:
                self.completed_batches += 1
            
            print(f"✅ Батч {batch_id}/{self.total_batches} подключен: {len(batch)} тикеров")
            logger.info(f"Батч {batch_id}/{self.total_batches} подключен: {len(batch)} тикеров")
        
        # URL для WebSocket Bybit
        ws_url = "wss://stream.bybit.com/v5/public/spot"
        
        ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        # Добавляем соединение в список
        with self.lock:
            self.ws_connections.append(ws)
        
        # Запускаем WebSocket в отдельном потоке
        def run_ws():
            ws.run_forever()
        
        ws_thread = threading.Thread(target=run_ws)
        ws_thread.daemon = True
        ws_thread.start()
    
    def start_all_websockets(self, tickers: List[str]):
        """Запускает WebSocket подключения для всех батчей"""
        # Создаем батчи по 10 тикетов
        batches = self.create_batches(tickers, 10)
        self.total_batches = len(batches)
        
        print(f"🔄 Создано {self.total_batches} батчей для подключения...")
        logger.info(f"Создано {self.total_batches} батчей для подключения")
        
        # Запускаем подключения с задержкой между ними
        for i, batch in enumerate(batches):
            self.start_websocket_for_batch(batch, i + 1)
            time.sleep(0.2)  # Задержка между подключениями
    
    def find_ticker(self, user_input: str) -> str:
        """Находит правильное название тикера по пользовательскому вводу"""
        user_input = user_input.upper().replace(" ", "")
        
        # Прямое совпадение
        if user_input in self.subscribed_tickers:
            return user_input
            
        # Пробуем добавить USDT если его нет
        if not user_input.endswith('USDT') and f"{user_input}USDT" in self.subscribed_tickers:
            return f"{user_input}USDT"
            
        # Ищем частичное совпадение
        for ticker in self.subscribed_tickers:
            if user_input == ticker.replace('USDT', ''):
                return ticker
                
        return user_input
    
    def log_user_request(self, user_id: str, username: str, message: str, source: str = "TG"):
        """Логирует запросы пользователей"""
        log_message = f"[{source}] {username} ({user_id}): {message}"
        print(f"👤 {log_message}")
        logger.info(log_message)
        
        # Сохраняем последние запросы для статистики
        with self.lock:
            self.user_requests[user_id] = {
                'username': username,
                'last_request': message,
                'timestamp': time.time(),
                'source': source
            }
    
    def get_ticker_data(self, symbol: str, user_id: str = None, username: str = None, source: str = "TG") -> dict:
        """Мгновенно возвращает актуальные данные по тикеру"""
        # Логируем запрос если передан user_id
        if user_id and username:
            self.log_user_request(user_id, username, symbol, source)
        
        # Находим правильное название тикера
        correct_symbol = self.find_ticker(symbol)
        
        with self.lock:
            data = self.current_data.get(correct_symbol)
        
        if data:
            # Форматируем красивые данные
            result = {
                'symbol': data.get('symbol', 'N/A'),
                'last_price': data.get('lastPrice', 'N/A'),
                'price_change_24h': data.get('price24hPcnt', 'N/A'),
                'high_price_24h': data.get('highPrice24h', 'N/A'),
                'low_price_24h': data.get('lowPrice24h', 'N/A'),
                'volume_24h': data.get('volume24h', 'N/A'),
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.get('ts', 0)/1000)) if data.get('ts') else 'N/A'
            }
            
            # Добавляем процент изменения
            if result['price_change_24h'] != 'N/A':
                result['price_change_percent_24h'] = f"{float(result['price_change_24h']) * 100:.2f}%"
            
            return result
        else:
            return {
                "error": f"Данные для {symbol} не найдены",
                "correct_symbol": correct_symbol,
                "tip": f"Подписано тикетов: {len(self.subscribed_tickers)}",
                "progress": f"Батчи: {self.completed_batches}/{self.total_batches}"
            }
    
    def get_stats(self) -> dict:
        """Возвращает статистику по сбору данных"""
        with self.lock:
            return {
                "available_tickers": len(self.available_tickers),
                "subscribed_tickers": len(self.subscribed_tickers),
                "current_data": len(self.current_data),
                "active_connections": len(self.ws_connections),
                "batch_progress": f"{self.completed_batches}/{self.total_batches}",
                "user_requests_count": len(self.user_requests)
            }
    
    def start_collection(self):
        """Запускает весь процесс сбора данных"""
        print("🚀 Запуск сбора данных Bybit...")
        logger.info("Запуск сбора данных Bybit")
        
        # Получаем список тикетов
        tickers = self.fetch_spot_tickers()
        if not tickers:
            print("❌ Не удалось получить тикеты")
            logger.error("Не удалось получить тикеты")
            return
        
        print(f"📊 Подключаемся ко всем {len(tickers)} тикетам...")
        logger.info(f"Подключаемся ко всем {len(tickers)} тикетам")
        
        # Запускаем все WebSocket подключения
        self.start_all_websockets(tickers)
        
        # Ждем завершения подключения всех батчей
        while self.completed_batches < self.total_batches:
            stats = self.get_stats()
            print(f"⏳ Прогресс: {stats['batch_progress']} батчей | Подписано тикетов: {stats['subscribed_tickers']}")
            time.sleep(2)
        
        print("\n" + "="*60)
        print("✅ ВСЕ ТИКЕТЫ ПОДПИСАНЫ!")
        print("="*60)
        stats = self.get_stats()
        print(f"📊 Статистика:")
        print(f"   • Доступно тикетов: {stats['available_tickers']}")
        print(f"   • Подписано тикетов: {stats['subscribed_tickers']}")
        print(f"   • Активных соединений: {stats['active_connections']}")
        print(f"   • Данных в памяти: {stats['current_data']}")
        print("💡 Можно вводить любой тикер (BTC, ETH, BTCUSDT, и т.д.)")
        print("="*60)
        logger.info("Все тикеты подписаны, система готова к работе")

class TelegramBot:
    def __init__(self, token: str, data_collector: BybitDataCollector):
        self.token = token
        self.data_collector = data_collector
        self.bot = telebot.TeleBot(token)
        self.setup_handlers()
    
    def setup_handlers(self):
        """Настраивает обработчики команд"""
        
        @self.bot.message_handler(commands=['start'])
        def start_handler(message: Message):
            user = message.from_user
            welcome_text = (
                f"Привет, {user.first_name}! 👋\n\n"
                "Я бот для отслеживания криптовалютных пар с Bybit в реальном времени.\n\n"
                "📊 Доступные команды:\n"
                "/ticker [symbol] - получить данные по тикеру (например: /ticker BTC или /ticker ETHUSDT)\n"
                "/stats - статистика системы\n"
                "/help - помощь\n\n"
                "Просто напиши название тикера (BTC, ETH, ADA и т.д.) и я покажу актуальные данные!"
            )
            self.bot.reply_to(message, welcome_text)
        
        @self.bot.message_handler(commands=['help'])
        def help_handler(message: Message):
            help_text = (
                "ℹ️ Помощь по использованию бота:\n\n"
                "📈 Получить данные по тикеру:\n"
                "   • Напиши просто 'BTC' или 'ETH'\n"
                "   • Или используй команду /ticker BTC\n"
                "   • Бот автоматически добавит USDT если нужно\n\n"
                "📊 Статистика системы:\n"
                "   • /stats - покажет сколько тикетов отслеживается\n\n"
                "🔄 Данные обновляются в реальном времени через WebSocket\n"
                "⚡ Ответ мгновенный - данные уже в памяти"
            )
            self.bot.reply_to(message, help_text)
        
        @self.bot.message_handler(commands=['stats'])
        def stats_handler(message: Message):
            stats = self.data_collector.get_stats()
            stats_text = (
                "📈 СТАТИСТИКА СИСТЕМЫ:\n\n"
                f"• Всего тикетов: {stats['available_tickers']}\n"
                f"• Подписано: {stats['subscribed_tickers']}\n"
                f"• Данных в памяти: {stats['current_data']}\n"
                f"• Активных соединений: {stats['active_connections']}\n"
                f"• Запросов пользователей: {stats['user_requests_count']}\n"
                f"• Прогресс: {stats['batch_progress']} батчей\n\n"
                "✅ Система работает в реальном времени"
            )
            self.bot.reply_to(message, stats_text)
        
        @self.bot.message_handler(commands=['ticker'])
        def ticker_command_handler(message: Message):
            user = message.from_user
            if not message.text or len(message.text.split()) < 2:
                self.bot.reply_to(message, "ℹ️ Использование: /ticker [symbol]\nНапример: /ticker BTC или /ticker ETHUSDT")
                return
            
            ticker_symbol = message.text.split()[1]
            self.send_ticker_data(message, user, ticker_symbol)
        
        @self.bot.message_handler(func=lambda message: True)
        def text_handler(message: Message):
            user = message.from_user
            message_text = message.text.strip()
            
            # Если сообщение похоже на тикер
            if len(message_text) <= 10 and message_text.replace(' ', '').isalnum():
                self.send_ticker_data(message, user, message_text)
            else:
                self.bot.reply_to(message, "ℹ️ Напиши тикер для получения данных (например: BTC, ETH, ADA)")
    
    def send_ticker_data(self, message: Message, user, ticker_symbol: str):
        """Отправляет данные по тикеру"""
        ticker_data = self.data_collector.get_ticker_data(
            ticker_symbol, 
            str(user.id), 
            user.first_name,
            "TG"
        )
        
        if 'error' in ticker_data:
            response_text = f"❌ {ticker_data['error']}\n\n💡 Подсказка: {ticker_data.get('tip', '')}"
        else:
            response_text = (
                f"📊 {ticker_data['symbol']}\n\n"
                f"💵 Цена: {ticker_data['last_price']}\n"
                f"📈 Изменение 24h: {ticker_data.get('price_change_percent_24h', 'N/A')}\n"
                f"🔼 Макс 24h: {ticker_data['high_price_24h']}\n"
                f"🔽 Мин 24h: {ticker_data['low_price_24h']}\n"
                f"📊 Объем 24h: {ticker_data['volume_24h']}\n"
                f"🕐 Обновлено: {ticker_data['timestamp']}"
            )
        
        self.bot.reply_to(message, response_text)
    
    def run(self):
        """Запускает Telegram бота"""
        try:
            print("🤖 Telegram бот запускается...")
            logger.info("Запуск Telegram бота")
            self.bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"❌ Ошибка запуска Telegram бота: {e}")
            logger.error(f"Ошибка запуска Telegram бота: {e}")

def read_telegram_token():
    """Читает токен Telegram бота из файла"""
    try:
        with open('teleg.txt', 'r') as file:
            token = file.read().strip()
            if not token:
                raise ValueError("Токен не найден в файле")
            return token
    except FileNotFoundError:
        print("❌ Файл teleg.txt не найден")
        logger.error("Файл teleg.txt не найден")
        return None
    except Exception as e:
        print(f"❌ Ошибка чтения токена: {e}")
        logger.error(f"Ошибка чтения токена: {e}")
        return None

def main():
    # Создаем сборщик данных
    collector = BybitDataCollector()
    
    # Запускаем сбор данных в отдельном потоке
    collection_thread = threading.Thread(target=collector.start_collection)
    collection_thread.daemon = True
    collection_thread.start()
    
    # Даем время на начальное подключение
    print("⏳ Ожидаем подключение тикетов...")
    time.sleep(10)
    
    # Пытаемся запустить Telegram бота
    token = read_telegram_token()
    if token:
        bot = TelegramBot(token, collector)
        bot_thread = threading.Thread(target=bot.run)
        bot_thread.daemon = True
        bot_thread.start()
        print("✅ Telegram бот запущен в отдельном потоке")
    else:
        print("❌ Telegram бот не запущен - не удалось получить токен")
    
    # Основной цикл для консольных запросов
    while True:
        try:
            print("\n" + "-"*40)
            print("1. Ввести тикер для данных")
            print("2. Показать статистику")
            print("3. Показать логи пользователей")
            print("4. Выход")
            choice = input("Выберите опцию (1/2/3/4): ").strip()
            
            if choice == '1':
                user_input = input("Введите тикер: ").strip()
                if user_input:
                    data = collector.get_ticker_data(user_input, "console", "ConsoleUser", "CONSOLE")
                    print(f"\n📊 Данные для {user_input}:")
                    print(json.dumps(data, indent=2, ensure_ascii=False))
                    
            elif choice == '2':
                stats = collector.get_stats()
                print(f"\n📈 Статистика системы:")
                print(f"   • Всего тикетов: {stats['available_tickers']}")
                print(f"   • Подписано: {stats['subscribed_tickers']}")
                print(f"   • Данных в памяти: {stats['current_data']}")
                print(f"   • Активных соединений: {stats['active_connections']}")
                print(f"   • Запросов пользователей: {stats['user_requests_count']}")
                print(f"   • Прогресс батчей: {stats['batch_progress']}")
                
            elif choice == '3':
                print(f"\n👤 Последние запросы пользователей:")
                for user_id, request_data in collector.user_requests.items():
                    time_ago = time.time() - request_data['timestamp']
                    print(f"   • {request_data['username']} ({user_id}) [{request_data['source']}]: {request_data['last_request']} ({int(time_ago)} сек. назад)")
                
            elif choice == '4' or choice.lower() == 'exit':
                print("👋 Выход...")
                logger.info("Завершение работы системы")
                break
            else:
                print("❌ Неверный выбор")
                
        except KeyboardInterrupt:
            print("\n👋 Выход...")
            logger.info("Завершение работы системы (KeyboardInterrupt)")
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            logger.error(f"Ошибка в основном цикле: {e}")

if __name__ == "__main__":
    main()