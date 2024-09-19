#     Copyright 2024. ThingsBoard
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.

import logging
from time import sleep, monotonic
from threading import Thread

from thingsboard_gateway.gateway.statistics.statistics_service import StatisticsService

TRACE_LOGGING_LEVEL = 5
logging.addLevelName(TRACE_LOGGING_LEVEL, "TRACE")


def init_logger(gateway, name, level, enable_remote_logging=False, is_connector_logger=False,
                is_converter_logger=False):
    """
    For creating a Logger with all config automatically
    Create a Logger manually only if you know what you are doing!
    """
    log = TbLogger(name=name, gateway=gateway, is_connector_logger=is_connector_logger,
                   is_converter_logger=is_converter_logger)

    if enable_remote_logging:
        from thingsboard_gateway.tb_utility.tb_handler import TBLoggerHandler
        remote_handler = TBLoggerHandler(gateway)
        log.addHandler(remote_handler)
        log.setLevel(gateway.main_handler.level)
        remote_handler.add_logger(name)
        remote_handler.activate()

    if hasattr(gateway, 'main_handler'):
        log.addHandler(gateway.main_handler)
        log.setLevel(gateway.main_handler.level)

    log_level_conf = level
    if log_level_conf:
        log_level = logging.getLevelName(log_level_conf)

        try:
            log.setLevel(log_level)
        except ValueError:
            log.setLevel(logging.NOTSET)

    return log


class TbLogger(logging.Logger):
    ALL_ERRORS_COUNT = 0
    IS_ALL_ERRORS_COUNT_RESET = False
    RESET_ERRORS_PERIOD = 60
    SEND_ERRORS_PERIOD = 5

    def __init__(self, name, gateway=None, level=logging.NOTSET, is_connector_logger=False, is_converter_logger=False,
                 connector_name=None):
        super(TbLogger, self).__init__(name=name, level=level)
        self.propagate = True
        self.parent = self.root
        self._gateway = gateway
        self._stopped = False
        self.__previous_number_of_errors = -1
        self.__previous_errors_sent_time = monotonic()
        self.__is_connector_logger = is_connector_logger
        self.__is_converter_logger = is_converter_logger
        logging.Logger.trace = TbLogger.trace

        if self.__is_connector_logger:
            self.connector_name = name
        elif connector_name:
            self.connector_name = connector_name
        elif self.__is_converter_logger and connector_name is None:
            raise ValueError("Connector name must be provided for connector logger")

        self.errors = 0
        self.attr_name = self.name + '_ERRORS_COUNT'
        self._is_on_init_state = True
        if self._gateway:
            self._send_errors()

        self._start_time = monotonic()
        self._reset_errors_thread = Thread(target=self._processing_errors, name='[LOGGER] Process Errors Thread',
                                           daemon=True)
        self._reset_errors_thread.start()

    def reset(self):
        """
        !!!Need to be called manually in the connector 'close' method!!!
        """
        TbLogger.ALL_ERRORS_COUNT = TbLogger.ALL_ERRORS_COUNT - self.errors
        self.errors = 0
        self._send_error_count()

    def stop(self):
        self.reset()
        self._stopped = True

    @property
    def gateway(self):
        return self._gateway

    @gateway.setter
    def gateway(self, gateway):
        self._gateway = gateway

    def _send_errors(self):
        is_tb_client = False

        while not self._gateway:
            sleep(1)

        while not is_tb_client:
            is_tb_client = hasattr(self._gateway, 'tb_client')
            sleep(1)

        if not TbLogger.IS_ALL_ERRORS_COUNT_RESET and self._gateway.tb_client is not None and self._gateway.tb_client.is_connected():
            self._gateway.send_telemetry({self.attr_name: 0, 'ALL_ERRORS_COUNT': 0}, quality_of_service=0)
            TbLogger.IS_ALL_ERRORS_COUNT_RESET = True
        self._is_on_init_state = False

    def _processing_errors(self):
        while not self._stopped:
            if (self.__previous_number_of_errors != self.errors
                    and monotonic() - self.__previous_errors_sent_time >= TbLogger.SEND_ERRORS_PERIOD):
                self.__previous_number_of_errors = self.errors
                self._send_error_count()
            elif monotonic() - self._start_time >= TbLogger.RESET_ERRORS_PERIOD:
                self.reset()
                self._start_time = monotonic()

            sleep(1)

    def trace(self, msg, *args, **kwargs):
        if self.isEnabledFor(TRACE_LOGGING_LEVEL):
            self._log(TRACE_LOGGING_LEVEL, msg, args, **kwargs)


    def error(self, msg, *args, **kwargs):
        kwargs['stacklevel'] = 2
        super(TbLogger, self).error(msg, *args, **kwargs)

        if self.__is_connector_logger:
            StatisticsService.count_connector_message(self.name, 'connectorsErrors')
        if self.__is_converter_logger:
            StatisticsService.count_connector_message(self.name, 'convertersErrors')

        self._add_error()

    def exception(self, msg, *args, **kwargs) -> None:
        attr_name = kwargs.pop('attr_name', None)
        kwargs['stacklevel'] = 2
        super(TbLogger, self).exception(msg, *args, **kwargs)

        if self.__is_connector_logger:
            StatisticsService.count_connector_message(self.name, 'connectorsErrors')
        if self.__is_converter_logger:
            StatisticsService.count_connector_message(self.name, 'convertersErrors')

        self._add_error()
        if attr_name is not None:
            self._send_error_count(error_attr_name=attr_name)

    def _add_error(self):
        TbLogger.ALL_ERRORS_COUNT += 1
        self.errors += 1

    def _send_error_count(self, error_attr_name=None):
        while self._is_on_init_state:
            sleep(.2)

        if self._gateway and hasattr(self._gateway, 'tb_client'):
            if error_attr_name:
                error_attr_name = error_attr_name + '_ERRORS_COUNT'
            else:
                error_attr_name = self.attr_name
            if self._gateway.tb_client is not None and self._gateway.tb_client.is_connected():
                self._gateway.send_telemetry(
                    {error_attr_name: self.errors, 'ALL_ERRORS_COUNT': TbLogger.ALL_ERRORS_COUNT})
