"""A minimal StatusNotifierWatcher, so Qt uses its D-Bus tray backend without a desktop.

Qt picks the D-Bus tray only when `org.kde.StatusNotifierWatcher` exists on the session bus and
reports a registered host. Qt reads that property through `QDBusInterface`, which introspects the
watcher first, so this stub answers `Introspect` as well as the watcher methods.
"""

import os
import sys

from jeepney import (
    DBusAddress,
    HeaderFields,
    Message,
    MessageType,
    new_error,
    new_method_return,
    new_signal,
)
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import DBusConnection, open_dbus_connection

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
PROPERTIES = "org.freedesktop.DBus.Properties"
INTROSPECTABLE = "org.freedesktop.DBus.Introspectable"
# `RequestName` returns 1 when this connection becomes the primary owner of the name.
PRIMARY_OWNER = 1

INTROSPECTION_XML = f"""<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="{INTROSPECTABLE}">
    <method name="Introspect"><arg name="xml" type="s" direction="out"/></method>
  </interface>
  <interface name="{PROPERTIES}">
    <method name="Get">
      <arg type="s" direction="in"/><arg type="s" direction="in"/>
      <arg type="v" direction="out"/>
    </method>
    <method name="GetAll">
      <arg type="s" direction="in"/><arg type="a{{sv}}" direction="out"/>
    </method>
  </interface>
  <interface name="{WATCHER_NAME}">
    <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
    <method name="RegisterStatusNotifierHost"><arg type="s" direction="in"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
    <signal name="StatusNotifierHostRegistered"/>
  </interface>
</node>
"""


def request_name(connection: DBusConnection, name: str) -> None:
    reply = connection.send_and_get_reply(message_bus.RequestName(name))
    if reply.body[0] != PRIMARY_OWNER:
        raise SystemExit(f"could not own {name}: RequestName returned {reply.body[0]}")


class Watcher:
    def __init__(self, connection: DBusConnection) -> None:
        self._connection = connection
        self._items: list[str] = []
        self._emitter = DBusAddress(WATCHER_PATH, interface=WATCHER_NAME)

    def properties(self) -> dict[str, tuple[str, list[str] | bool | int]]:
        # A real host is not needed: nothing draws the icon, the driver reads it over D-Bus.
        return {
            "RegisteredStatusNotifierItems": ("as", list(self._items)),
            "IsStatusNotifierHostRegistered": ("b", True),
            "ProtocolVersion": ("i", 0),
        }

    def handle(self, message: Message) -> None:
        fields = message.header.fields
        interface = fields.get(HeaderFields.interface)
        member = fields.get(HeaderFields.member)
        if member == "Introspect":
            self._connection.send(new_method_return(message, "s", (INTROSPECTION_XML,)))
        elif member == "Get" and interface == PROPERTIES:
            _interface, name = message.body
            value = self.properties().get(name)
            if value is None:
                # An uncaught `KeyError` would end the stub, and the tray would lose its host.
                error = "org.freedesktop.DBus.Error.UnknownProperty"
                self._connection.send(new_error(message, error, "s", (name,)))
            else:
                self._connection.send(new_method_return(message, "v", (value,)))
        elif member == "GetAll" and interface == PROPERTIES:
            self._connection.send(new_method_return(message, "a{sv}", (self.properties(),)))
        elif member == "RegisterStatusNotifierItem":
            self._register_item(message)
        elif member == "RegisterStatusNotifierHost":
            self._connection.send(new_method_return(message))
            self._connection.send(new_signal(self._emitter, "StatusNotifierHostRegistered"))
        else:
            self._connection.send(
                new_error(message, "org.freedesktop.DBus.Error.UnknownMethod", "s", (member,))
            )

    def _register_item(self, message: Message) -> None:
        (service,) = message.body
        # The spec lets an item pass an object path and leave the bus name implied by the sender.
        if service.startswith("/"):
            service = f"{message.header.fields[HeaderFields.sender]}{service}"
        if service not in self._items:
            self._items.append(service)
        print(f"watcher: registered {service}", file=sys.stderr, flush=True)
        self._connection.send(new_method_return(message))
        self._connection.send(
            new_signal(self._emitter, "StatusNotifierItemRegistered", "s", (service,))
        )


def main() -> None:
    connection = open_dbus_connection(bus="SESSION")
    watcher = Watcher(connection)
    request_name(connection, WATCHER_NAME)
    # A desktop always has a host on the bus. Owning a host name keeps the bus close to that.
    request_name(connection, f"org.kde.StatusNotifierHost-{os.getpid()}")
    print("watcher: ready", file=sys.stderr, flush=True)
    while True:
        message = connection.receive()
        if message.header.message_type == MessageType.method_call:
            watcher.handle(message)


if __name__ == "__main__":
    main()
