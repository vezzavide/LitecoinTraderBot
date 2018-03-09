# Automated trading bot that operates on Binance exchange
#
#  BOT COMMANDS:
#     start - Set API keys and initialize bot
#     start_trading - Buy at market price and start trading
#     stop_trading - Stop trading
#     state - Check current state and settings
#     settings - Change trading parameters
#     current_price - Check current market price

# TODOS:

import telegram
import logging
from telegram import (ReplyKeyboardMarkup, ReplyKeyboardRemove)
from telegram.error import TelegramError
from binance.client import Client
from binance.exceptions import BinanceAPIException
from requests.exceptions import ConnectionError
from binance.enums import *
from telegram.ext import (Updater, CommandHandler, ConversationHandler, MessageHandler, Filters)
from datetime import datetime
import time
import configparser

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


class TraderBot:
    token = 'telegram_bot_token'
    # TODO: set admin ID
    admin_id = '12345678'

    # Trading states
    INIT = 0            # Binance client is not set, key and secret need to be sent
    WAITING = 1         # Client is set and verified but trading is not activated
    BUY_PLACED = 2      # A buy order has been scheduled but it isn't filled yet        ###  STATES WITH
    BOUGHT = 3          # Buy order completed and a sell order is yet to be scheduled    ##  AUTOMATED
    SELL_PLACED = 4     # A sell order has been scheduled but it isn't filled yet        ##  TRADING
    SOLD = 5            # Sell order completed and a buy order is yet to be scheduled   ###  ACTIVATED

    trading_state = INIT

    # Trading parameters in USDT
    sell_increment = None
    buy_decrement = None

    # Last prices
    last_bought_price = None
    last_sold_price = None

    # element 0: older order
    # element 1: newer order
    last_two_orders = [None, None]

    debug = True
    updater = Updater(token=token)
    dispatcher = updater.dispatcher
    sell_scheduled = False
    buy_scheduled = False
    api_key = None
    api_secret = None
    binance_client = None

    # /start conversation states
    GET_START_CONFIRMATION = 0
    SET_API_KEY = 1
    SET_API_SECRET = 2

    # /settings conversation states
    SET_SELL_INCREMENT = 0
    SET_BUY_DECREMENT = 1

    # /start_trading conversation states
    GET_START_TRADING_CONFIRMATION = 0

    # /stop_trading conversation states
    GET_STOP_TRADING_CONFIRMATION = 0

    # Scheduled action variables
    sell_increment_changed = False
    buy_decrement_changed = False

    def __init__(self):
        self.log("Bot started")

        # Loads settings
        config = configparser.ConfigParser()
        config.read('settings')
        self.sell_increment = float(config['SETTINGS']['sell_increment'])
        self.buy_decrement = float(config['SETTINGS']['buy_decrement'])

        # Conversation handler for /start command
        # TODO: find why fallback doesn't work (if you don't use chat filters it works, but they're too important
        # to give up. I kept cancel, but it's useless
        start_conversation_handler = ConversationHandler(
            entry_points=[CommandHandler('start', self.start_command, filters=Filters.chat(self.admin_id))],
            states={
                self.GET_START_CONFIRMATION: [MessageHandler(Filters.chat(self.admin_id), self.get_start_confirmation)],
                self.SET_API_KEY: [MessageHandler(Filters.chat(self.admin_id), self.set_api_key)],
                self.SET_API_SECRET: [MessageHandler(Filters.chat(self.admin_id), self.set_api_secret)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel_command, filters=Filters.chat(self.admin_id))]
        )
        self.dispatcher.add_handler(start_conversation_handler)

        # Conversation handler for /settings command
        settings_conversation_handler = ConversationHandler(
            entry_points=[CommandHandler('settings', self.settings_command, filters=Filters.chat(self.admin_id))],
            states={
                self.SET_SELL_INCREMENT: [MessageHandler(Filters.chat(self.admin_id), self.set_sell_increment)],
                self.SET_BUY_DECREMENT: [MessageHandler(Filters.chat(self.admin_id), self.set_buy_decrement)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel_command, filters=Filters.chat(self.admin_id))]
        )
        self.dispatcher.add_handler(settings_conversation_handler)

        # /state command handler
        state_command_handler = CommandHandler('state', self.state_command, filters=Filters.chat(self.admin_id))
        self.dispatcher.add_handler(state_command_handler)

        # # TODO: remove this command in production
        # # /set_state command handler
        # set_state_command_handler = CommandHandler('set_state', self.set_state_command,
        #                                            filters=Filters.chat(self.admin_id),
        #                                            pass_args=True)
        # self.dispatcher.add_handler(set_state_command_handler)

        # Conversation handler for /start_trading command
        start_trading_conversation_handler = ConversationHandler(
            entry_points=[CommandHandler('start_trading', self.start_trading_command, filters=Filters.chat(self.admin_id))],
            states={
                self.GET_START_TRADING_CONFIRMATION: [MessageHandler(Filters.chat(self.admin_id), self.get_start_trading_confirmation)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel_command, filters=Filters.chat(self.admin_id))]
        )
        self.dispatcher.add_handler(start_trading_conversation_handler)

        # Conversation handler for /stop_trading command
        stop_trading_conversation_handler = ConversationHandler(
            entry_points=[CommandHandler('stop_trading', self.stop_trading_command, filters=Filters.chat(self.admin_id))],
            states={
                self.GET_STOP_TRADING_CONFIRMATION: [MessageHandler(Filters.chat(self.admin_id), self.get_stop_trading_confirmation)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_command, filters=Filters.chat(self.admin_id))]
        )
        self.dispatcher.add_handler(stop_trading_conversation_handler)

        # /current_price command handler
        current_price_command_handler = CommandHandler('current_price', self.current_price_command, filters=Filters.chat(self.admin_id))
        self.dispatcher.add_handler(current_price_command_handler)

        # Sends start up message
        self.start_up()


    def start_command(self, bot, update):
        self.log("/start command received")

        if self.trading_state == self.INIT:
            message = ("We are going to initialize and authorize me.\n"
                       + "To start, send me your Binance *API key*:")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            return self.SET_API_KEY

        elif self.trading_state == self.WAITING:
            reply_keyboard = [['Yes', 'No']]
            message = ("To initialize me again you'll have to *re-enter API key and secret*, are you sure?")
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
                             parse_mode=telegram.ParseMode.MARKDOWN)
            return self.GET_START_CONFIRMATION

        else:
            message = ("To initialize me again and re-enter API key and secret you have to stop "
                       + "the automated trading first with the /stop_trading command.")
            bot.send_message(chat_id=self.admin_id, text=message)
            return ConversationHandler.END

    def get_start_confirmation(self, bot, update):
        if update.message.text == 'Yes':
            self.log("Start command confirmed")
            message = "Alright, send me your *Binance API key*:"
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardRemove(),
                             parse_mode=telegram.ParseMode.MARKDOWN)
            return self.SET_API_KEY
        else:
            self.log("Start command canceled")
            message = "Alrigth, action canceled."
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardRemove(),
                             parse_mode=telegram.ParseMode.MARKDOWN)
            return ConversationHandler.END


    def set_api_key(self, bot, update):
        self.api_key = update.message.text
        message = ("Good. Now send me your *Binance API secret*:")
        bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
        self.log("API key has been set")
        return self.SET_API_SECRET


    def set_api_secret(self, bot, update):
        self.api_secret = update.message.text
        self.log("API secret has been set")
        binance_client = Client(self.api_key, self.api_secret)
        try:
            binance_client.get_account()
        except BinanceAPIException as e:
            message = ("*Error from Binance!* API key or API secret are probably *wrong*.")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            message = "Try again with the /start command."
            bot.send_message(chat_id=self.admin_id, text=message)

            self.log("Error from Binance: " + str(e))
            self.api_key = None
            self.api_secret = None
            self.binance_client = None
            self.trading_state = self.INIT
        except Exception as e:
            message = ("*Error!* Something went wrong!")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            message = "Try again with the /start command."
            bot.send_message(chat_id=self.admin_id, text=message)
            self.log("Exception while trying Binance client: " + str(e))
            self.api_key = None
            self.api_secret = None
            self.binance_client = None
            self.trading_state = self.INIT
        else:
            # API is alright!
            self.binance_client = binance_client
            message = ("Good! Your API key and secret have been validated, *I'm ready and connected to Binance*.")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            message = ("If you want to start the automated trading, send me the /start_trading command.\n"
                       + "At any moment, if you want to take a look at my current state, send me the /state command.\n"
                       + "If you want to change the automated trading parameters and settings, send me the "
                       + "/settings command.")
            bot.send_message(chat_id=self.admin_id, text=message)
            self.log("Api works fine.")
            self.trading_state = self.WAITING
        finally:
            return ConversationHandler.END


    def settings_command(self, bot, update):
        self.log("/settings command received")
        message = ("Alright. We are going to set the *increment* (in USDT) necessary to automatically sell "
                   + "and the *decrement* (in USDT) necessary to automatically buy. _All numbers sent to "
                   + "me must be just numbers, without symbols of any kind_. "
                   + "Decimal numbers are allowed.")
        bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
        message = ("Send me the *sell increment*:")
        bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
        return self.SET_SELL_INCREMENT

    def set_sell_increment(self, bot, update):
        self.log("sell_increment received")
        try:
            sell_increment = abs(float(update.message.text))
        except Exception as e:
            message = ("*Wrong format!* Send me just a number, without symbols of any kind.")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            message = "To try again send me the /settings command."
            bot.send_message(chat_id=self.admin_id, text=message)

            self.log("sell_increment in wrong format")
            return ConversationHandler.END
        else:
            self.sell_increment = sell_increment
            message = ("Sell increment has been successfully set to *+$" + str(self.sell_increment) + "*"
                       + "\nNow send me the *buy decrement*:")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            self.log("sell_increment set")
            return self.SET_BUY_DECREMENT

    def set_buy_decrement(self, bot, update):
        self.log("buy decrement received")
        try:
            buy_decrement = abs(float(update.message.text))
        except Exception as e:
            message = ("*Wrong format*! Send me just a number, without symbols of any kind.")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            message  = "To try again send me the /settings command."
            bot.send_message(chat_id=self.admin_id, text=message)
            self.log("buy_decrement in wrong format")
            return ConversationHandler.END
        else:
            self.buy_decrement = buy_decrement

            # Saves settings to file
            config = configparser.ConfigParser()
            config['SETTINGS'] = {}
            config['SETTINGS']['sell_increment'] = str(self.sell_increment)
            config['SETTINGS']['buy_decrement'] = str(self.buy_decrement)
            with open('settings', 'w') as settings_file:
                config.write(settings_file)

            message = ("Buy decrement has been successfully set to *-$" + str(self.buy_decrement) + "*"
                       + "\nThese new settings will be applied to any open order (if possible) "
                       + "and to any new order from now on.")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            self.log("buy_decrement set")
            self.sell_increment_changed = True
            self.buy_decrement_changed = True
            return ConversationHandler.END


    def state_command(self, bot, update):
        self.log("/state command received")
        state = self.trading_state
        if (state == self.INIT) or (state == self.WAITING):
            order_info = "  unknown"
        else:
            last_order = self.binance_client.get_order(symbol='LTCUSDT', orderId=str(self.get_last_order()))
            order_info = self.order_info_to_str(last_order)

        if self.binance_client == None:
            account_info = "  unknown"
        else:
            usdt_balance = self.binance_client.get_asset_balance(asset='USDT')
            ltc_balance = self.binance_client.get_asset_balance(asset='LTC')
            account_info = ("  • USDT balance:\n"
                            + "    • free: " + usdt_balance['free'] + "\n"
                            + "    • locked: " + usdt_balance['locked'] + "\n"
                            + "  • LTC balance:\n"
                            + "    • free: " + ltc_balance['free'] + "\n"
                            + "    • locked: " + ltc_balance['locked'])

        message = ("• *Current state*: " + self.state_to_str() + ".\n"
                   + "• *Sell increment*: +$" + str(self.sell_increment)
                   + "\n• *Buy decrement*: -$" + str(self.buy_decrement)
                   + "\n• *Account balance*:\n" + account_info
                   + "\n• *Last order*:\n" + order_info)
        bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)


    def state_to_str(self):
        state = self.trading_state
        if state == self.INIT:
            return "Trading OFF, not connected to Binance"
        elif state == self.WAITING:
            return "Trading OFF, connected to Binance"
        elif state == self.BUY_PLACED:
            return "Trading ON, buy order placed and yet to be filled"
        elif state == self.BOUGHT:
            return "Trading ON, buy order filled"
        elif state == self.SELL_PLACED:
            return "Trading ON, sell order placed and yet to be filled"
        elif state == self.SOLD:
            return "Trading ON, sell order filled"


    def start_trading_command(self, bot, update):
        self.log("/start_trading received")

        # Check states before start trading
        state = self.trading_state

        if state == self.INIT:
            message = ("To start the automated trading, you first have to initialize me and send me the Binance API "
                       + "key and API secret.\nTo to that, send me the /start command.")
            bot.send_message(chat_id=self.admin_id, text=message)
            self.log("/start_trading denied, INIT state")
            return ConversationHandler.END

        elif state != self.WAITING:
            message = ("Trading is already activated!")
            bot.send_message(chat_id=self.admin_id, text=message)
            self.log("/start_trading denied, already activated")
            return ConversationHandler.END

        elif state == self.WAITING:
            message = ("Before starting the automated trading *make sure that*:\n"
                       + "• *your USDT account is as full as possible and your LTC account is as empty as possible*, "
                       + "manually selling all your Litecoins if necessary.\n"
                       + "• *there aren't any open orders*. If there are, close them.\n\n"
                       + "To start the automated trading, I am going to place a buy order at current market price.\n"
                       + "*Do you wish to continue*?")
            reply_keyboard = [['Yes', 'No']]
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
                             parse_mode=telegram.ParseMode.MARKDOWN)
            self.log("Asked for /start_trading confirmation")
            return self.GET_START_TRADING_CONFIRMATION


    def get_start_trading_confirmation(self, bot, update):
        if update.message.text == 'Yes':
            self.log("/start_trading confirmed")
            message = "Alright, *trading started*!\nI'm going to put the first buy order and see if it goes through."
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardRemove(),
                             parse_mode=telegram.ParseMode.MARKDOWN)

            # Start automated trading with first sell order
            try:
                self.log("I'm going to place a buy order")
                usdt_balance = float(self.binance_client.get_asset_balance(asset='USDT')['free'])
                self.log("Current USDT balance is: " + str(usdt_balance))
                rounded_usdt_balance = usdt_balance - 1 # I'll leave 1 dollar on the balance just to have a little margin
                self.log("USDT balance - 1 is: " + str(rounded_usdt_balance))
                current_ltc_price = float(self.binance_client.get_symbol_ticker(symbol='LTCUSDT')['price'])
                self.log("Current LTC price is: " + str(current_ltc_price))
                ltc_to_buy = rounded_usdt_balance / (current_ltc_price + 0.5) # 0.5 added to make sure order goes through
                self.log("I want to buy rounded_usdt_balance/(current_ltc_price + 0.5) Litecoins: " + str(ltc_to_buy))
                ltc_to_buy = "{:0.0{}f}".format(ltc_to_buy - 0.00001, 5) # rounding LTC here too, for margin
                self.log("I will actually send a request for buying LTC: " + str(ltc_to_buy))
                try:
                    last_placed_order = self.binance_client.order_limit_buy(symbol='LTCUSDT',
                                                                            quantity=ltc_to_buy,
                                                                            price=str(current_ltc_price))
                    self.set_last_order(last_placed_order['orderId'])
                    self.log("The order went fine, here it is:\n\t" + str(last_placed_order))
                    if last_placed_order['status'] == 'FILLED':
                        self.trading_state = self.BOUGHT
                        self.log("State changed to BOUGHT")
                        message = ("*Buy order successfully placed and filled*:\n"
                                   + self.order_info_to_str(last_placed_order))
                        bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
                    else:
                        self.trading_state = self.BUY_PLACED
                        self.log("State changed to BUY_PLACED")
                        message = ("*Buy order sucessfully placed*:\n"
                                   + self.order_info_to_str(last_placed_order))
                        bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
                except Exception as e:
                    self.log("Exception while placing order: " + str(e))
                    message = ("*Error while placing order*!\nError message: " + str(e) +
                               "\n\n*Automated trading stopped*.")
                    bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
                    self.trading_state = self.WAITING

            except BinanceAPIException as e:
                self.log("BinanceAPIExcpetion: " + str(e))
                message = ("*Error from Binance*!\nError message: " + str(e) + "\n\n*Automated trading stopped*.")
                bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
                self.trading_state = self.WAITING

        else:
            self.log("/start_trading canceled, automated trading NOT started")
            message = "Alright, automated trading *not activated*."
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardRemove(),
                             parse_mode=telegram.ParseMode.MARKDOWN)

        return ConversationHandler.END


    def stop_trading_command(self, bot, update):
        self.log("/stop_trading received")

        # Check states before start trading
        state = self.trading_state
        if (state == self.INIT) or (state == self.WAITING):
            message = ("Trading is already deactivated!")
            bot.send_message(chat_id=self.admin_id, text=message)
            self.log("/stop_trading denied, trading already deactivated")
            return ConversationHandler.END

        else:
            message = ("Are you sure you want to *stop the automated trading*?")
            reply_keyboard = [['Yes', 'No']]
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
                             parse_mode=telegram.ParseMode.MARKDOWN)
            self.log("Asked for confirmation of /stop_trading")
            return self.GET_STOP_TRADING_CONFIRMATION


    def get_stop_trading_confirmation(self, bot, update):
        if update.message.text == 'Yes':
            self.log("/stop_trading confirmed")
            message = "Alrigth, *automated trading stopped*!\nIf there's an open order left, it will stay on."
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardRemove(),
                             parse_mode=telegram.ParseMode.MARKDOWN)
            self.trading_state = self.WAITING
        else:
            self.log("/stop_trading canceled, automated trading stays on")
            message = "Alrigth, *automated trading stays ON*."
            bot.send_message(chat_id=self.admin_id, text=message,
                             reply_markup=ReplyKeyboardRemove(),
                             parse_mode=telegram.ParseMode.MARKDOWN)
        return ConversationHandler.END


    def current_price_command(self, bot, update):
        self.log("/current price command received")
        state = self.trading_state
        if state == self.INIT:
            message = ("I'm not connected to Binance yet! You first have to initialize me and send me the Binance API "
                       + "key and API secret.\nTo to that, send me the /start command.")
            bot.send_message(chat_id=self.admin_id, text=message)
            self.log("/current_price denied, INIT state")
        else:
            current_price = float(self.binance_client.get_symbol_ticker(symbol='LTCUSDT')['price'])
            message = ("Current LTC/USDT price: *$" + str(current_price) + "*")
            bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
            self.log("current price sent")


    # # TODO: remove this in production
    # def set_state_command(self, bot, update, args):
    #     state_to_set = int(args[0])
    #     self.trading_state = state_to_set
    #     self.log("State changed to " + str(state_to_set))


    def order_info_to_str(self, order):
        if order is None:
            return "None"
        else:
            order_str = ("  • Order ID: " + str(order['orderId'])
                         + "\n  • Side: " + order['side']
                         + "\n  • Price in USDT: " + order['price']
                         + "\n  • Quantity in LTC: " + order['origQty']
                         + "\n  • Status: " + order['status'])
            return order_str

    def start_up(self):
        try:
            message = ("I just rebooted. For security reasons, you have to initialize and authorize me again, "
                       + "using the /start command.")
            self.updater.bot.send_message(chat_id=self.admin_id, text=message)
        except TelegramError as e:
            self.log("Telegram error on start_up function: " + str(e))


    def cancel_command(self, bot, update):
        message = "Alright, action canceled."
        bot.send_message(chat_id=self.admin_id, text=message)
        self.log("/cancel command received.")
        return ConversationHandler.END


    def log(self, text):
        if self.debug:
            now = datetime.now()
            print(str(now) + ':  ' + text)


    def set_last_order(self, order):
        # Removes older order
        self.last_two_orders.pop(0)
        # Append new one
        self.last_two_orders.append(order)


    def get_last_order(self):
        return self.last_two_orders[1]


    def get_penultimate_order(self):
        return self.last_two_orders[0]


    def run(self):
        self.updater.start_polling()
        # All the meaningful operations need to happen inside this loop, with the help of "schedule" variables.
        # If some event needs to do something important (e.g. buy, sell, stop trading), it needs to schedule
        # such operation with a proper variable and then let the main loop take care of it.
        # That's to guarantee atomicity and avoid overlapping operations.

        while True:
            time.sleep(1)
            state = self.trading_state

            if state == self.INIT:
                self.init_function()
                self.buy_decrement_changed = False
                self.sell_increment_changed = False

            elif state == self.WAITING:
                self.waiting_function()
                self.buy_decrement_changed = False
                self.sell_increment_changed = False

            elif state == self.BUY_PLACED:
                self.buy_placed_function()
                self.sell_increment_changed = False
                # Here I don't reset buy_decrement because in this state I WANT to know if it is necessary to
                # change an order

            elif state == self.BOUGHT:
                self.bought_function()
                self.buy_decrement_changed = False
                self.sell_increment_changed = False

            elif state == self.SELL_PLACED:
                self.sell_placed_function()
                self.buy_decrement_changed = False
                # Here I don't reset sell_increment because in this state I WANT to know if it is necessary to
                # change an order

            elif state == self.SOLD:
                self.sold_function()
                self.buy_decrement_changed = False
                self.sell_increment_changed = False



    # MAIN LOOP FUNCTIONS
    #
    # Orders possible states:
    #     NEW
    #     PARTIALLY_FILLED
    #     FILLED
    #     CANCELED
    #     PENDING_CANCEL(currently unused)
    #     REJECTED
    #     EXPIRED

    def init_function(self):
        # Nothing to do here
        pass

    def waiting_function(self):
        # Nothing to do here as well
        pass

    def buy_placed_function(self):
        # Gets last placed order and checks if it
        try:
            last_order = self.binance_client.get_order(symbol='LTCUSDT', orderId=str(self.get_last_order()))
            last_order_status = last_order['status']
            if last_order_status == 'FILLED':
                self.trading_state = self.BOUGHT
                self.log("Order filled: " + str(last_order))
                self.log("State changed to BOUGHT")
                message = ("*Buy order successfully filled*:\n" + self.order_info_to_str(last_order))
                self.updater.bot.send_message(chat_id=self.admin_id,
                                              text=message,
                                              parse_mode=telegram.ParseMode.MARKDOWN)
            elif last_order_status == 'NEW':
                # The order is present, but yet to be filled
                if self.buy_decrement_changed:
                    try:
                        message = ("The buy decrement has been changed, so *I'll try to modify the current open buy order*.")
                        self.updater.bot.send_message(chat_id=self.admin_id,
                                                      text=message,
                                                      parse_mode=telegram.ParseMode.MARKDOWN)
                        self.log("Buy decrement changed, I need to delete the current buy open order and make a new one")
                        self.binance_client.cancel_order(symbol='LTCUSDT', orderId=self.get_last_order())
                        # Goes back in time of one step, te penultimate order becomes the last
                        self.last_two_orders[1] = self.last_two_orders[0]
                        self.last_two_orders[0] = None
                        self.trading_state = self.SOLD
                    except Exception as e:
                        self.log("Error while trying to change current open order: " + str(e))
                        message = ("Since you changed the buy decrement, I tried to modify the current open buy "
                                   + " order, but *something went wrong and I couldn't do it*.")
                        self.updater.bot.send_message(chat_id=self.admin_id,
                                                      text=message,
                                                      parse_mode=telegram.ParseMode.MARKDOWN)
                    finally:
                        self.buy_decrement_changed = False
            elif last_order_status == 'PARTIALLY_FILLED':
                # The order is present, has been partially filled and I can only wait
                pass
            else:
                # Any other state
                self.trading_state = self.WAITING
                self.log("Last order has a state not expected: " + str(last_order))
                self.log("State changed to WAITING")
                message = ("*Error!*\nThere's something wrong with my last order."
                           + "Maybe you canceled the order from the Binance site? Maybe Binance rejected it?\n"
                           + "Here's the order in question:\n" + self.order_info_to_str(last_order)
                           + "\n\nYou should take a look at your Binance trading page and see what happend."
                           + "\nSince I don't know what's going on, *I stopped the automated trading*")
                self.updater.bot.send_message(chat_id=self.admin_id,
                                              text=message,
                                              parse_mode=telegram.ParseMode.MARKDOWN)
        except Exception as e:
            self.log("Exception while checking last order: " + str(e))


    def bought_function(self):
        try:
            # Now I have to schedule a new sell order
            self.log("I'm going to place the next sell order")
            sell_increment = self.sell_increment
            last_order = self.binance_client.get_order(symbol='LTCUSDT', orderId=str(self.get_last_order()))
            last_bought_price = float(last_order['price'])
            next_sell_price = last_bought_price + sell_increment
            ltc_to_sell = float(self.binance_client.get_asset_balance(asset='LTC')['free'])
            self.log("Current LTC balance is: " + str(ltc_to_sell))
            ltc_to_sell = "{:0.0{}f}".format(ltc_to_sell - 0.00001, 5)  # rounding LTC here too, for margin
            self.log("But I will send a request for buying LTC (rounded): " + str(ltc_to_sell))
            self.log("The price I want to buy at is: " + str(next_sell_price))
            last_placed_order = self.binance_client.order_limit_sell(symbol='LTCUSDT',
                                                                     quantity=ltc_to_sell,
                                                                     price=str(next_sell_price))
            self.set_last_order(last_placed_order['orderId'])
            self.log("The order went fine, here it is:\n\t" + str(last_placed_order))
            self.trading_state = self.SELL_PLACED
            self.log("State changed to SELL_PLACED")
            message = ("I'm going to sell again at $" + str(last_bought_price) + " + $" + str(sell_increment)
                    + " = $" + str(next_sell_price) + ".\n"
                    + "*Sell order successfully placed*:\n" + self.order_info_to_str(last_placed_order))
            self.updater.bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
        except Exception as e:
            self.log("Exception while placing next sell order: " + str(e))


    def sell_placed_function(self):
        # Get last placed order
        try:
            last_order = self.binance_client.get_order(symbol='LTCUSDT', orderId=str(self.get_last_order()))
            last_order_status = last_order['status']
            if last_order_status == 'FILLED':
                self.trading_state = self.SOLD
                self.log("Order filled: " + str(last_order))
                self.log("State changed to SOLD")
                message = ("*Sell order successfully filled*:\n" + self.order_info_to_str(last_order))
                self.updater.bot.send_message(chat_id=self.admin_id,
                                              text=message,
                                              parse_mode=telegram.ParseMode.MARKDOWN)
            elif last_order_status == 'NEW':
                # The order is present, but yet to be filled
                if self.sell_increment_changed:
                    try:
                        message = ("The sell increment has been changed, so *I'll try to modify the current open sell order*.")
                        self.updater.bot.send_message(chat_id=self.admin_id,
                                                      text=message,
                                                      parse_mode=telegram.ParseMode.MARKDOWN)
                        self.log("Sell increment changed, I need to delete the current sell open order and make a new one")
                        self.binance_client.cancel_order(symbol='LTCUSDT', orderId=self.get_last_order())
                        # Goes back in time of one step, te penultimate order becomes the last
                        self.last_two_orders[1] = self.last_two_orders[0]
                        self.last_two_orders[0] = None
                        self.trading_state = self.BOUGHT
                    except Exception as e:
                        self.log("Error while trying to change current open sell order: " + str(e))
                        message = ("I tried to modify the current open buy "
                                   + " order, but *something went wrong and I couldn't do it*.")
                        self.updater.bot.send_message(chat_id=self.admin_id,
                                                      text=message,
                                                      parse_mode=telegram.ParseMode.MARKDOWN)
                    finally:
                        self.sell_increment_changed = False
                pass
            elif last_order_status == 'PARTIALLY_FILLED':
                # The order is present, has been partially filled and I can only wait
                pass
            else:
                # Any other state
                self.trading_state = self.WAITING
                self.log("Last order has a state not expected: " + str(last_order))
                self.log("State changed to WAITING")
                message = ("*Error!*\nThere's something wrong with my last order."
                           + "Maybe you canceled the order from the Binance site?  Maybe Binance rejected it?\n"
                           + "Here's the order in question:\n" + self.order_info_to_str(last_order)
                           + "\n\nYou should take a look at your Binance trading page and see what happend."
                           + "\nSince I don't know what's going on, *I stopped the automated trading*.")
                self.updater.bot.send_message(chat_id=self.admin_id,
                                              text=message,
                                              parse_mode=telegram.ParseMode.MARKDOWN)
        except Exception as e:
            self.log("Exception while checking last order: " + str(e))


    def sold_function(self):
        try:
            # Now I have to schedule a new buy order

            last_order = self.binance_client.get_order(symbol='LTCUSDT', orderId=str(self.get_last_order()))
            last_sold_price = float(last_order['price'])
            buy_decrement = self.buy_decrement
            next_buy_price = last_sold_price - buy_decrement
            self.log("I want to buy at: " + str(next_buy_price))
            usdt_balance = float(self.binance_client.get_asset_balance(asset='USDT')['free'])
            self.log("Current USDT balance is: " + str(usdt_balance))
            rounded_usdt_balance = usdt_balance - 1  # I'll leave 1 dollar on the balance just to have a little margin
            self.log("USDT balance - 1 is: " + str(rounded_usdt_balance))
            ltc_to_buy = rounded_usdt_balance / next_buy_price
            self.log("I want to buy rounded_usdt_balance/next_buy Litecoins: " + str(ltc_to_buy))
            ltc_to_buy = "{:0.0{}f}".format(ltc_to_buy - 0.00001, 5)  # rounding LTC here too, for margin
            self.log("I will actually send a request for buying LTC (rounded): " + str(ltc_to_buy))
            last_placed_order = self.binance_client.order_limit_buy(symbol='LTCUSDT',
                                                                    quantity=ltc_to_buy,
                                                                    price=str(next_buy_price))
            self.set_last_order(last_placed_order['orderId'])
            self.log("The order went fine, here it is:\n\t" + str(last_placed_order))
            self.trading_state = self.BUY_PLACED
            self.log("State changed to BUY_PLACED")
            message = ("I'm going to buy again at $" + str(last_sold_price) + " - $" + str(buy_decrement)
                    + " = $" + str(next_buy_price) + ".\n"
                    + "*Buy order successfully placed*:\n" + self.order_info_to_str(last_placed_order))
            self.updater.bot.send_message(chat_id=self.admin_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
        except Exception as e:
            self.log("Exception while placing next buy order: " + str(e))


if __name__ == '__main__':

    traderBot = TraderBot()
    traderBot.run()
