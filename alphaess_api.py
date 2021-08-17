"""Alpha ESS Unofficial API."""

import json
import logging
from os import path
from pickle import dump as pickle_dump, load as pickle_load
import sys
from threading import Lock
from time import time

import requests
from voluptuous import Optional

_LOGGER = logging.getLogger(__name__)


# ---------------------------
#   AlphaESSAPI
# ---------------------------
class AlphaESSAPI:
    """Handle all communication with Alpha ESS."""

    def __init__(self, host, username, password):
        """Initialize the Alpha ESS API."""
        self._host = host
        self._username = username
        self._password = password
        self._protocol = "https"
        self._resource = f"{self._protocol}://{self._host}/api/"

        self.lock = Lock()

        self._connection = None
        self._connected = False
        self._reconnected = False
        self._connection_epoch = 0
        self._connection_retry_sec = 58
        self.error = None
        self.connection_error_reported = False
        self.accounting_last_run = None
        self.accesstoken = None

    # ---------------------------
    #   has_reconnected
    # ---------------------------
    def has_reconnected(self) -> bool:
        """Check if API has reconnected."""
        if self._reconnected:
            self._reconnected = False
            return True

        return False

    # ---------------------------
    #   connection_check
    # ---------------------------
    def connection_check(self) -> bool:
        """Check if API is connected."""
        if not self._connected or not self._connection:
            if self._connection_epoch > time() - self._connection_retry_sec:
                return False

            if not self.connect():
                return False

        return True

    # ---------------------------
    #   disconnect
    # ---------------------------
    def disconnect(self) -> bool:
        """Disconnect API."""
        self.error = ""
        self.lock.acquire()

        try:
            response = self._connection.post(
                f"{self._resource}/Account/Logout",
                headers={"Authorization": f"Bearer {self.accesstoken}"},
            )
            data = response.json()

            self.lock.release

            if data["info"] != "Success":
                _LOGGER.error(
                    "AlphaESS %s failed disconnect: Error %s", self._host, data["info"]
                )
            else:
                _LOGGER.debug("AlphaESS %s connection closed", self._host)

        except requests.exceptions.RequestException as e:
            _LOGGER.error("AlphaESS %s request error: %s", self._host, e)
            self.lock.release()

        except Exception as e:
            _LOGGER.error("AlphaESS Unexpected error: %s", e)
            self.lock.release()

        finally:
            self.connected = False
            self._reconnected = False
            self._connection = None
            self._connection_epoch = 0
            return True

    # ---------------------------
    #   connect
    # ---------------------------
    def connect(self) -> bool:
        """Connect API."""
        self.error = ""
        self._connected = False
        self._connection_epoch = time()
        self._connection = requests.Session()

        self.lock.acquire()
        try:
            response = self._connection.post(
                f"{self._resource}/Account/Login",
                data=json.dumps(
                    {"username": self._username, "password": self._password}
                ),
                headers={"Content-Type": "application/json"},
            )
            data = response.json()

            if data["info"] != "Success":
                _LOGGER.error("AlphaESS %s failed : Error %s", self._host, data["info"])
                self.error_to_strings("%s" % data["info"])
                self._connection = None
                self.lock.release()
                return False
            else:
                if "AccessToken" not in data["data"]:
                    _LOGGER.error(
                        "AlphaESS %s failed : Error %s",
                        self._host,
                        "no AccessToken was received",
                    )
                    self.error_to_strings("%s" % data["info"])
                    self._connection = None
                    self.lock.release()
                    return False
                else:
                    self.accesstoken = data["data"]["AccessToken"]
                    self._connected = True
                    self._reconnected = True
                    self.lock.release()
                    _LOGGER.debug("AlphaESS %s connected", self._host)
                    return True

        except requests.exceptions.RequestException as e:
            _LOGGER.error("AlphaESS %s request error: %s", self._host, e)
            self.error_to_strings("%s" % e)
            self._connection = None
            self.lock.release()
            return False

        except Exception as e:
            _LOGGER.error("AlphaESS Unexpected error: %s", e)
            self.error_to_strings("%s" % e)
            self._connection = None
            self.lock.release()
            return False

    # ---------------------------
    #   error_to_strings
    # ---------------------------
    def error_to_strings(self, error=""):
        """Translate error output to error string."""
        self.error = "cannot_connect"
        if "Incorrect User Name or Password" in error:
            self.error = "invalid_auth"

        if "certificate verify failed" in error:
            self.error = "ssl_verify_failed"

    # ---------------------------
    #   connected
    # ---------------------------
    def connected(self) -> bool:
        """Return connected boolean."""
        return self._connected

    # ---------------------------
    #   ess_list - retrieve ESS list by serial number from Alpha ESS
    # ---------------------------

    def ess_list(self) -> Optional(list):
        """Retrieve ESS list by serial number from Alpha ESS"""

        if not self.connection_check():
            return None

        self.lock.acquire()

        try:
            _LOGGER.debug("AlphaESS %s /api/Account/GetCustomMenuESSlist", self._host)
            response = self._connection.get(
                f"{self._resource}/Account/GetCustomMenuESSlist",
                headers={"Authorization": f"Bearer {self.accesstoken}"},
            )

            data = response.json()

            if data["info"] != "Success":
                _LOGGER.error("AlphaESS %s failed : Error %s", self._host, data["info"])
                self.error_to_strings("%s" % data["info"])
                self._connection = None
                self.lock.release()
                return False
            else:
                _LOGGER.debug("AlphaESS %s /api/Account/GetCustomMenuESSlist response: %s",self._host,data)

        except requests.exceptions.RequestException as e:
            _LOGGER.error("AlphaESS %s request error: %s", self._host, e)
            self.disconnect()
            self.lock.release()

        except Exception as e:
            _LOGGER.error("AlphaESS Unexpected error: %s", e)
            self.disconnect()
            self.lock.release()
        
        self.lock.release()
        if data["data"] is not None:
            return data["data"]
        else:
            return None

    # ---------------------------
    #   GetSecondDataBySn - retrieve instantaneous from Alpha ESS
    # ---------------------------

    def GetSecondDataBySn (self,serial) -> Optional(list):
        """Retrieve ESS list by serial number from Alpha ESS"""

        if not self.connection_check():
            return None

        self.lock.acquire()

        try:
            _LOGGER.debug("AlphaESS %s api/ESS/GetSecondDataBySn?sys_sn=%s", self._host, serial)
            response = self._connection.get(
                f"{self._resource}/ESS/GetSecondDataBySn?sys_sn={serial}",
                headers={"Authorization": f"Bearer {self.accesstoken}"},
            )

            data = response.json()

            if data["info"] != "Success":
                _LOGGER.error("AlphaESS %s failed : Error %s", self._host, data["info"])
                self.error_to_strings("%s" % data["info"])
                self._connection = None
                self.lock.release()
                return False
            else:
                _LOGGER.debug("AlphaESS %s /api/Account/GetCustomMenuESSlist response: %s",self._host,data)

        except requests.exceptions.RequestException as e:
            _LOGGER.error("AlphaESS %s request error: %s", self._host, e)
            self.disconnect()
            self.lock.release()

        except Exception as e:
            _LOGGER.error("AlphaESS Unexpected error: %s", e)
            self.disconnect()
            self.lock.release()
        
        self.lock.release()
        if data["data"] is not None:
            return data["data"]
        else:
            return None        


#     # ---------------------------
#     #   query
#     # ---------------------------
#     def query(self, service, method, params=None, options=None) -> Optional(list):
#         """Retrieve data from OMV."""
#         if not self.connection_check():
#             return None

#         if not params:
#             params = {}

#         if not options:
#             options = {"updatelastaccess": False}

#         self.lock.acquire()
#         try:
#             _LOGGER.debug(
#                 "AlphaESS %s query: %s, %s, %s, %s",
#                 self._host,
#                 service,
#                 method,
#                 params,
#                 options,
#             )
#             response = self._connection.post(
#                 self._resource,
#                 data=json.dumps(
#                     {
#                         "service": service,
#                         "method": method,
#                         "params": params,
#                         "options": options,
#                     }
#                 ),
#                 verify=self._ssl_verify,
#             )

#             data = response.json()
#             _LOGGER.debug("AlphaESS %s query response: %s", self._host, data)

#         except (
#             requests.exceptions.ConnectionError,
#             json.decoder.JSONDecodeError,
#         ) as api_error:
#             _LOGGER.warning("AlphaESS %s unable to fetch data", self._host)
#             self.disconnect("query", api_error)
#             self.lock.release()
#             return None
#         except:
#             self.disconnect("query")
#             self.lock.release()
#             return None

#         self.lock.release()
#         if data is not None and data["error"] is not None:
#             error_message = data["error"]["message"]
#             error_code = data["error"]["code"]
#             if (
#                 error_code == 5001
#                 or error_code == 5002
#                 or error_message == "Session not authenticated."
#                 or error_message == "Session expired."
#             ):
#                 _LOGGER.debug("AlphaESS %s session expired", self._host)
#                 if self.connect():
#                     return self.query(service, method, params, options)

#         return data["response"]
