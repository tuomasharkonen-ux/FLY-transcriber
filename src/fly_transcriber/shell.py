"""The native shell: menubar icon, popover and dashboard window.

Everything the user sees is the web UI in ``static/``, hosted in ``WKWebView``s
instead of a browser tab. Left-clicking the menubar icon opens a popover with
``popover.html``; the full dashboard opens in an app window. Right-click (or
Control-click) shows a small native menu.

The popover's web view is transparent, so the system popover material (Liquid
Glass on macOS 26, vibrancy before that) shows through the page.

Pages talk back through ``window.webkit.messageHandlers.fly`` for the few
things only the shell can do: resize the popover, close it, open the window.
Everything else goes through the HTTP API, same as in a browser.

All of this must run on the main thread. ``call_on_main`` is the way in from
other threads.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

import objc
from AppKit import (
    NSAffineTransform,
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseUp,
    NSEventModifierFlagControl,
    NSEventModifierFlagShift,
    NSEventTypeRightMouseUp,
    NSFont,
    NSImage,
    NSImageLeading,
    NSLineCapStyleRound,
    NSMenu,
    NSMenuItem,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSRectEdgeMinY,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSViewController,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSURL, NSMakePoint, NSMakeRect, NSObject, NSTimer, NSURLRequest
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration

POPOVER_WIDTH = 360
POPOVER_HEIGHT = (140, 640)  # min, max; the page reports what it needs
WINDOW_SIZE = (1040, 760)

#: ``(title, callback)``, ``(title, [submenu…])`` or ``None`` for a separator.
MenuSpec = list


def call_on_main(fn: Callable, *args) -> None:
    """Run ``fn`` on the main thread as soon as it is free."""
    AppHelper.callAfter(fn, *args)


def activate() -> None:
    """Bring this accessory app forward, so its alerts and windows get focus.

    Without this an alert can open *behind* the frontmost app and block the
    main thread with nothing visible.
    """
    try:
        NSApplication.sharedApplication().activate()
    except Exception:
        pass


def alert(title: str, message: str = "", ok: str = "OK", cancel: str | None = None) -> bool:
    """A modal alert. True when the first (``ok``) button was chosen."""
    activate()
    panel = NSAlert.alloc().init()
    panel.setMessageText_(title)
    panel.setInformativeText_(message)
    panel.addButtonWithTitle_(ok)
    if cancel:
        panel.addButtonWithTitle_(cancel)
    return panel.runModal() == NSAlertFirstButtonReturn


# -- Objective-C glue --------------------------------------------------------


class _Action(NSObject):
    """A target/action receiver that calls a Python function."""

    def initWithCallback_(self, callback):
        self = objc.super(_Action, self).init()
        if self is not None:
            self._callback = callback
        return self

    def fire_(self, sender):
        self._callback(sender)


class _AppDelegate(NSObject):
    """Lets the app veto termination (logout, shutdown, ``NSApp.terminate``)."""

    def initWithGuard_reopen_(self, guard, reopen):
        self = objc.super(_AppDelegate, self).init()
        if self is not None:
            self._guard = guard
            self._reopen = reopen
        return self

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
        # Launching an already-running app again (Spotlight, Finder, the Dock).
        self._reopen()
        return True

    def applicationShouldTerminate_(self, _app):
        # NSTerminateCancel = 0, NSTerminateNow = 1
        return 1 if self._guard() else 0


class _Bridge(NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
    """Receives ``window.webkit.messageHandlers.fly.postMessage(...)``."""

    def initWithHandler_(self, handler):
        self = objc.super(_Bridge, self).init()
        if self is not None:
            self._handler = handler
        return self

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        try:
            data = dict(message.body())
        except (TypeError, ValueError):
            return
        self._handler(data)


# -- the menubar glyph ------------------------------------------------------


def _fly_glyph() -> NSImage:
    """The FLY logo (a microphone with fly wings) as a template image.

    Drawn in code from the logo's 32-unit geometry (see ``Logo`` in app.js and
    favicon.svg) so it stays crisp at any scale. Template images are drawn by
    the system in the menubar's text colour; the wings are part-transparent so
    they read as wings rather than a solid blob.
    """
    height = 18.0
    scale = height / 25.5
    width = 28 * scale

    def point(x, y):
        return NSMakePoint((x - 2) * scale, (y - 2) * scale)

    def draw(_rect):
        for cx, angle in ((9.3, -56), (22.7, 56)):
            wing = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(-3.2 * scale, -6.6 * scale, 6.4 * scale, 13.2 * scale)
            )
            place = NSAffineTransform.transform()
            place.translateXBy_yBy_((cx - 2) * scale, (10.5 - 2) * scale)
            place.rotateByDegrees_(angle)
            wing.transformUsingAffineTransform_(place)
            NSColor.colorWithWhite_alpha_(0, 0.5).setFill()
            wing.fill()

        NSColor.blackColor().setFill()
        NSColor.blackColor().setStroke()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(10.4 * scale, 3 * scale, 7.2 * scale, 12.6 * scale), 3.6 * scale, 3.6 * scale
        ).fill()
        stand = NSBezierPath.bezierPath()
        stand.setLineWidth_(1.9 * scale)
        stand.setLineCapStyle_(NSLineCapStyleRound)
        stand.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            point(16, 15), 6.6 * scale, 180, 0, True
        )
        stand.moveToPoint_(point(16, 21.6))
        stand.lineToPoint_(point(16, 25))
        stand.moveToPoint_(point(12.6, 25))
        stand.lineToPoint_(point(19.4, 25))
        stand.stroke()
        return True

    image = NSImage.imageWithSize_flipped_drawingHandler_((width, height), True, draw)
    image.setTemplate_(True)
    image.setAccessibilityDescription_("FLY")
    return image


def _symbol(name: str, description: str) -> NSImage | None:
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, description)
    if image is not None:
        image.setTemplate_(True)
    return image


# -- the shell --------------------------------------------------------------


class Shell:
    """Owns every native UI object. Create and use it on the main thread."""

    def __init__(
        self,
        base_url: str,
        menu: Callable[[], MenuSpec],
        confirm_quit: Callable[[], bool] = lambda: True,
        on_reopen: Callable[[], None] = lambda: None,
    ) -> None:
        self._app = NSApplication.sharedApplication()
        self._app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        # NSApplication holds its delegate weakly, so keep a reference.
        self._delegate = _AppDelegate.alloc().initWithGuard_reopen_(
            confirm_quit, on_reopen
        )
        self._app.setDelegate_(self._delegate)
        self._base_url = base_url
        self._menu_spec = menu
        # PyObjC does not keep Python-side targets alive; these do.
        self._keep: list = []
        self._window = None
        self._window_view = None
        self._menu_targets: list = []  # replaced each time the menu is built

        self._icons = {
            "idle": _fly_glyph(),
            "busy": _symbol("waveform", "Processing"),
            "failed": _symbol("exclamationmark.triangle", "Failed"),
        }
        self._icons["recording"] = self._icons["idle"]

        self._app.setMainMenu_(self._key_menu())

        bar = NSStatusBar.systemStatusBar()
        self._item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self._item.button()
        button.setImagePosition_(NSImageLeading)
        button.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(0, 0))
        button.setTarget_(self._target(self._clicked))
        button.setAction_("fire:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self.set_status("idle")

        self._popover = NSPopover.alloc().init()
        self._popover.setBehavior_(NSPopoverBehaviorTransient)
        self._popover.setAnimates_(True)
        self._popover.setContentSize_((POPOVER_WIDTH, 420))
        self._popover_view = self._webview(transparent=True)
        controller = NSViewController.alloc().init()
        controller.setView_(self._popover_view)
        self._popover.setContentViewController_(controller)
        if base_url:
            self._load(self._popover_view, base_url + "popover.html")

    # -- public ---------------------------------------------------------------

    def run(self) -> None:
        # runEventLoop only installs its Ctrl-C handler when it creates the
        # NSApplication itself, and this one already exists.
        AppHelper.installMachInterrupt()
        AppHelper.runEventLoop()

    def quit(self) -> None:
        AppHelper.stopEventLoop()

    def every(self, seconds: float, fn: Callable[[], None]) -> None:
        target = self._target(lambda _timer: fn())
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            seconds, target, "fire:", None, True
        )

    def set_status(self, kind: str, title: str = "", tooltip: str = "FLY") -> None:
        """Update the menubar icon: ``idle``, ``recording``, ``busy`` or ``failed``."""
        button = self._item.button()
        button.setImage_(self._icons.get(kind) or self._icons["idle"])
        button.setTitle_(f" {title}" if title else "")
        button.setContentTintColor_(
            NSColor.systemRedColor() if kind == "recording" else None
        )
        button.setToolTip_(tooltip)

    def close_popover(self) -> None:
        if self._popover.isShown():
            self._popover.performClose_(None)

    def open_window(self, route: str = "#/") -> None:
        """Show the full dashboard in an app window, at a hash route."""
        if not self._base_url:
            return
        self.close_popover()
        if self._window is None:
            self._window = self._make_window()
            self._load(self._window_view, self._base_url + route)
        else:
            self._eval(self._window_view, f"location.hash = {json.dumps(route)}")
        activate()
        self._window.makeKeyAndOrderFront_(None)

    # -- status item ----------------------------------------------------------

    def _clicked(self, _sender) -> None:
        event = self._app.currentEvent()
        secondary = event is not None and (
            event.type() == NSEventTypeRightMouseUp
            or event.modifierFlags() & NSEventModifierFlagControl
        )
        if secondary or not self._base_url:
            self._show_menu()
        elif self._popover.isShown():
            self._popover.performClose_(None)
        else:
            self._show_popover()

    def _show_popover(self) -> None:
        button = self._item.button()
        activate()
        self._popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSRectEdgeMinY)
        window = self._popover_view.window()
        if window is not None:
            window.makeKeyWindow()
            window.makeFirstResponder_(self._popover_view)
        self._eval(self._popover_view, "window.dispatchEvent(new Event('fly:shown'))")

    def _show_menu(self) -> None:
        self.close_popover()
        self._menu_targets = []
        self._item.setMenu_(self._build_menu(self._menu_spec()))
        self._item.button().performClick_(None)
        # Detach again, or every left-click would open the menu too.
        self._item.setMenu_(None)

    def _build_menu(self, spec: MenuSpec) -> NSMenu:
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for entry in spec:
            if entry is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            title, action = entry
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            if isinstance(action, list):
                item.setSubmenu_(self._build_menu(action))
            elif action is None:
                item.setEnabled_(False)
            else:
                target = _Action.alloc().initWithCallback_(action)
                self._menu_targets.append(target)
                item.setTarget_(target)
                item.setAction_("fire:")
            menu.addItem_(item)
        return menu

    def _key_menu(self) -> NSMenu:
        """A main menu that is never shown, only there for key equivalents.

        An accessory app has no visible menu bar, but Cocoa still routes ⌘C,
        ⌘V, ⌘A, ⌘Z and ⌘W through the main menu. Without one, text fields in
        the popover would not take copy or paste.
        """
        bar = NSMenu.alloc().init()
        edit = NSMenu.alloc().initWithTitle_("Edit")
        for title, action, key, shift in (
            ("Undo", "undo:", "z", False),
            ("Redo", "redo:", "z", True),
            ("Cut", "cut:", "x", False),
            ("Copy", "copy:", "c", False),
            ("Paste", "paste:", "v", False),
            ("Select All", "selectAll:", "a", False),
            ("Close Window", "performClose:", "w", False),
        ):
            item = edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
            if shift:
                item.setKeyEquivalentModifierMask_(
                    item.keyEquivalentModifierMask() | NSEventModifierFlagShift
                )
        holder = NSMenuItem.alloc().init()
        holder.setSubmenu_(edit)
        bar.addItem_(holder)
        return bar

    # -- web views ------------------------------------------------------------

    def _webview(self, transparent: bool) -> WKWebView:
        config = WKWebViewConfiguration.alloc().init()
        bridge = _Bridge.alloc().initWithHandler_(self._message)
        self._keep.append(bridge)
        config.userContentController().addScriptMessageHandler_name_(bridge, "fly")
        frame = NSMakeRect(0, 0, POPOVER_WIDTH, 420)
        view = WKWebView.alloc().initWithFrame_configuration_(frame, config)
        if transparent:
            # Not public API, but the long-standing way to let what is behind a
            # WKWebView show through; the page's own background is transparent.
            view.setValue_forKey_(False, "drawsBackground")
            view.setUnderPageBackgroundColor_(NSColor.clearColor())
        if os.environ.get("FLY_DEBUG"):
            view.setInspectable_(True)  # Safari → Develop → this Mac
        return view

    @staticmethod
    def _load(view: WKWebView, url: str) -> None:
        view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    @staticmethod
    def _eval(view: WKWebView, script: str) -> None:
        view.evaluateJavaScript_completionHandler_(script, None)

    def _make_window(self) -> NSWindow:
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, *WINDOW_SIZE), style, NSBackingStoreBuffered, False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("FLY")
        window.setMinSize_((560, 480))
        window.center()
        window.setFrameAutosaveName_("FLYDashboard")
        self._window_view = self._webview(transparent=False)
        window.setContentView_(self._window_view)
        return window

    def _message(self, data: dict) -> None:
        kind = data.get("type")
        if kind == "resize":
            low, high = POPOVER_HEIGHT
            height = max(low, min(high, float(data.get("height") or 0)))
            if abs(self._popover.contentSize().height - height) >= 1:
                self._popover.setContentSize_((POPOVER_WIDTH, height))
        elif kind == "close":
            self.close_popover()
        elif kind == "open":
            self.open_window(str(data.get("route") or "#/"))

    def _target(self, callback: Callable) -> _Action:
        target = _Action.alloc().initWithCallback_(callback)
        self._keep.append(target)
        return target
