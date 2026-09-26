// Riftstone runtime: why the game closed.
//
// A normal exit is the game's own teardown: its message loop ends, WinMain returns, the process exits
// (Steam's overlay log then shows DirectInput released, the Direct3D device released, the overlay
// detaching).  Afterwards nothing says what ended the loop, so the runtime watches it happen:
//
//   window messages  WH_CALLWNDPROC and WH_GETMESSAGE hooks on the thread of the game's window.  They
//                    only look: Alt+F4 (the key, then SC_CLOSE, then WM_CLOSE), the close button or the
//                    window menu (SC_CLOSE clicked, or chosen by keyboard), a WM_CLOSE, SC_CLOSE,
//                    WM_DESTROY or WM_QUIT from outside the game (posted, or sent from another thread),
//                    Windows ending the session (WM_ENDSESSION)
//   the quit flag    (DDDA build 2364871, exit_sites.h) the game's own exit -- Exit Game on the title
//                    screen, or the quit prompt of its start-up save check -- sets sApp+0x266C and the
//                    loop ends with no message at all; read every 50 ms by the watchdog thread
//
// The first thing that closes the game is the reason.  loader.log gets an `exit` line at once,
// riftstone\runtime-state.ini [session] gets end / end_detail / end_uptime_ms at once (after
// WM_ENDSESSION Windows may end the process without its teardown), and the exit summary names it.
// [loader] exit_reason = 0 turns the watch off.
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

enum End {
    END_NONE, END_ALT_F4, END_CLOSE_BUTTON, END_WINDOW_MENU, END_CLOSE_MESSAGE, END_SESSION, END_EXIT_MENU,
    END_EXIT_REQUEST, END_WINDOW_DESTROYED, END_QUIT_MESSAGE, END_FATAL, END_SELF, END_UNKNOWN, END_COUNT
};
// The codes are the contract with riftstone/runtime.py (END_REASONS); the words read after "ended by".
static const struct { const wchar_t* code; const wchar_t* text; } ENDS[END_COUNT] = {
    {L"", L""},
    {L"alt-f4", L"Alt+F4"},
    {L"close-button", L"the window's close button (or Close in its title-bar menu, clicked)"},
    {L"window-menu", L"Close in the window's menu, chosen with the keyboard"},
    {L"close-message", L"a close message from outside the game (another program, or a plugin)"},
    {L"session-end", L"Windows ending the session (shut down, restart, sign-out, or an installer closing programs)"},
    {L"exit-menu", L"the game's own exit (Exit Game on the title screen, or the quit prompt of its start-up save check)"},
    {L"exit-request", L"the game's exit request with no close message before it (its debug Exit command)"},
    {L"window-destroyed", L"a window of the game being destroyed (the game quits whenever one is)"},
    {L"quit-message", L"a quit message (WM_QUIT) with no close before it (another program, or a plugin)"},
    {L"fatal-error", L"the game's fatal-error message (it stops itself with exit(1))"},
    {L"self-exit", L"the game itself, with no message asking it to close"},
    {L"unknown", L"nothing seen (the game window's messages were not watched)"},
};

// How a message reached the game's window.
enum How { POSTED, SENT_HERE, SENT_FROM_OUTSIDE };

static const wchar_t* HowText(How h) {
    return h == POSTED ? L"posted" : h == SENT_HERE ? L"sent on the game's thread" : L"sent from another thread";
}

static const ULONGLONG CHAIN_MS = 2000;         // the key, SC_CLOSE and WM_CLOSE of one close
static const ULONGLONG CONFIRM_MS = 5000;       // a WM_CLOSE the game acted on (it does so at once)

static BOOL g_on = FALSE;
static volatile LONG g_claimed = 0;             // the first reason stands
static volatile LONG g_end = END_NONE;
static wchar_t g_detail[800];
static volatile LONG g_watching = 0;            // the hooks are on the game window's thread
static BOOL g_cannotWatch = FALSE;
static HWND g_window;
static HHOOK g_callHook, g_msgHook;
static volatile LONG g_quitSeen = 0;            // WM_QUIT reached the game's loop
static BOOL g_flagSeen = FALSE;                 // the watchdog thread's
static BOOL g_flagClearSeen = FALSE;            // the quit flag read clear once (the watchdog thread's)
static unsigned g_ticks = 0;

typedef BOOL(WINAPI* GetInputSource_t)(INPUT_MESSAGE_SOURCE*);
static GetInputSource_t g_inputSource;          // Windows 8 and later

// The close in progress; the game window's thread only.
static struct {
    ULONGLONG keyAt;                            // WM_SYSKEYDOWN F4 with Alt
    wchar_t keyFrom[96];
    ULONGLONG sysAt;                            // WM_SYSCOMMAND SC_CLOSE
    How sysHow;
    LPARAM sysPoint;
    wchar_t sysContext[320];                    // who was in front when an SC_CLOSE came from outside
    ULONGLONG closeAt;                          // WM_CLOSE: what it means, confirmed when the game acts on it
    End closeEnd;
    wchar_t closeDetail[700];
    BOOL askedEnd, destroyLogged;
} g_ui;

// ---- who asked, when a close comes from outside the game's thread -------------------------------
// Windows does not say which program sent or posted a message.  What it does say, at the moment the
// message arrives: the window in front and its program, whether the game's own window was in front or
// minimized, how long ago the last keyboard or mouse input was, and the window under the pointer.  The
// taskbar's Close window (explorer in front), Task Manager's End task (Task Manager in front), Steam's
// Stop (Steam in front) and a program closing the game while you play (the game in front, input a
// moment ago) read differently here; runtime.py says it in a sentence.  Reads only.
static void WindowOwner(HWND w, wchar_t* out, size_t cap) {
    if (!w) {
        wcsncpy_s(out, cap, L"no window", _TRUNCATE);
        return;
    }
    wchar_t cls[80] = L"";
    GetClassNameW(w, cls, _countof(cls));
    DWORD pid = 0;
    GetWindowThreadProcessId(w, &pid);
    wchar_t name[MAX_PATH] = L"";
    if (pid == GetCurrentProcessId()) {
        wcscpy_s(name, L"the game");
    } else {
        HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
        DWORD n = _countof(name);
        if (h && QueryFullProcessImageNameW(h, 0, name, &n)) {
            const wchar_t* slash = wcsrchr(name, L'\\');
            if (slash) memmove(name, slash + 1, (wcslen(slash + 1) + 1) * sizeof(wchar_t));
        } else {
            _snwprintf_s(name, _countof(name), _TRUNCATE, L"process %lu", pid);   // elevated, or already gone
        }
        if (h) CloseHandle(h);
    }
    _snwprintf_s(out, cap, _TRUNCATE, L"%s [%s]", name, cls);
}

static void OutsideContext(wchar_t* out, size_t cap) {
    HWND front = GetForegroundWindow();
    wchar_t frontText[120], underText[120], idle[40];
    WindowOwner(front, frontText, _countof(frontText));
    POINT pt;
    HWND under = GetCursorPos(&pt) ? WindowFromPoint(pt) : NULL;
    if (under) under = GetAncestor(under, GA_ROOT);
    WindowOwner(under, underText, _countof(underText));
    LASTINPUTINFO li = {sizeof li};
    if (GetLastInputInfo(&li)) {
        DWORD ms = GetTickCount() - li.dwTime;
        _snwprintf_s(idle, _countof(idle), _TRUNCATE, L"%lu.%lu s", ms / 1000, (ms / 100) % 10);
    } else {
        wcscpy_s(idle, L"an unknown time");
    }
    DWORD frontPid = 0;
    if (front) GetWindowThreadProcessId(front, &frontPid);
    const wchar_t* game = frontPid == GetCurrentProcessId() ? L"in front"
                          : g_window && IsIconic(g_window) ? L"minimized" : L"not in front";
    _snwprintf_s(out, cap, _TRUNCATE, L"at that moment: in front %s; the game's window %s; the last keyboard or mouse "
                 L"input %s before; the pointer over %s", frontText, game, idle, underText);
}

static void WithContext(wchar_t* d, size_t cap, const wchar_t* what, const wchar_t* context) {
    _snwprintf_s(d, cap, _TRUNCATE, L"%s; %s", what, context);
}

static void Record(End e, const wchar_t* detail) {
    if (!g_on || e == END_NONE || InterlockedCompareExchange(&g_claimed, 1, 0) != 0) return;
    int stage;
    if (CurrentStage(&stage))                   // DDDA 2364871: where the player was (the game's own reader)
        _snwprintf_s(g_detail, _countof(g_detail), _TRUNCATE, L"%s; in stage %d", detail ? detail : L"", stage);
    else
        wcsncpy_s(g_detail, _countof(g_detail), detail ? detail : L"", _TRUNCATE);
    InterlockedExchange(&g_end, e);
    ULONGLONG up = UptimeMs();
    LogLine(L"exit     %s, after %llu min %llu s: %s", ENDS[e].text, up / 60000, (up / 1000) % 60, g_detail);
    StabilityRecordEnd(ENDS[e].code, g_detail, up);
    wchar_t note[256];
    _snwprintf_s(note, _countof(note), _TRUNCATE, L"closing: %s", ENDS[e].text);
    LiveNote("notes", note);
}

// A window of the game's own class: its class window procedure is in the game's executable (DDDA's is
// 0x00DF1550).  Asked in both character sets: the class's own one returns the procedure, the other a handle.
static BOOL GameClassWindow(HWND w) {
    if (!w) return FALSE;
    if (InGameImage((DWORD_PTR)GetClassLongPtrW(w, GCLP_WNDPROC))) return TRUE;
    return InGameImage((DWORD_PTR)GetClassLongPtrA(w, GCLP_WNDPROC));
}

static BOOL GameWindow(HWND w) { return w && (w == g_window || GameClassWindow(w)); }

// Where the Alt+F4 key came from.  Only a key press from the input queue has a source; a message someone
// posts reads as "unavailable" (measured), and a sent one has none.
static void KeySource(How how, wchar_t* out, size_t cap) {
    const wchar_t* s = L"a source Windows 7 does not report";
    if (how != POSTED) {
        s = L"a message sent to the window, not a key press";
    } else if (g_inputSource) {
        INPUT_MESSAGE_SOURCE src = {};
        if (g_inputSource(&src)) {
            if (src.originId == IMO_INJECTED) s = L"input a program injected (a macro tool, a controller mapper, remote control)";
            else if (src.originId == IMO_HARDWARE) s = src.deviceType == IMDT_KEYBOARD ? L"the keyboard" : L"a device that is not a keyboard";
            else if (src.originId == IMO_SYSTEM) s = L"Windows itself";
            else s = L"a message posted to the window, not a key press";
        }
    }
    wcsncpy_s(out, cap, s, _TRUNCATE);
}

// WM_CLOSE reached a game window: what sent it.  The game acts on it at once (its exit request sends
// WM_DESTROY while WM_CLOSE is still being handled), which confirms it.
static void ArmClose(ULONGLONG now, How how) {
    End e;
    wchar_t d[700], what[300], ctx[320];
    BOOL sys = g_ui.sysAt && now - g_ui.sysAt < CHAIN_MS;
    BOOL key = sys && g_ui.keyAt && g_ui.keyAt <= g_ui.sysAt && g_ui.sysAt - g_ui.keyAt < CHAIN_MS;
    if (key) {
        e = END_ALT_F4;
        _snwprintf_s(d, _countof(d), _TRUNCATE, L"WM_SYSKEYDOWN F4 with Alt (from %s), then SC_CLOSE (%s), then WM_CLOSE",
                     g_ui.keyFrom, HowText(g_ui.sysHow));
    } else if (sys && g_ui.sysHow != SENT_HERE) {
        e = END_CLOSE_MESSAGE;
        _snwprintf_s(what, _countof(what), _TRUNCATE, L"SC_CLOSE %s to the game's window with no Alt+F4 before it "
                     L"(another program, e.g. the taskbar's Close window, or a plugin), then WM_CLOSE",
                     HowText(g_ui.sysHow));
        WithContext(d, _countof(d), what, g_ui.sysContext);
    } else if (sys && g_ui.sysPoint != 0 && g_ui.sysPoint != (LPARAM)-1) {
        e = END_CLOSE_BUTTON;
        _snwprintf_s(d, _countof(d), _TRUNCATE, L"SC_CLOSE clicked at %d,%d (the title bar's close button, or Close in "
                     L"its menu), then WM_CLOSE", (int)(short)LOWORD(g_ui.sysPoint), (int)(short)HIWORD(g_ui.sysPoint));
    } else if (sys) {
        e = END_WINDOW_MENU;
        _snwprintf_s(d, _countof(d), _TRUNCATE, L"SC_CLOSE chosen by keyboard (%s), then WM_CLOSE",
                     g_ui.sysPoint ? L"an accelerator" : L"a menu key");
    } else if (how == POSTED) {
        e = END_CLOSE_MESSAGE;
        OutsideContext(ctx, _countof(ctx));
        WithContext(d, _countof(d), L"WM_CLOSE posted to the game's window (the game never posts it; another program or "
                    L"a plugin did)", ctx);
    } else if (how == SENT_FROM_OUTSIDE) {
        e = END_CLOSE_MESSAGE;
        OutsideContext(ctx, _countof(ctx));
        WithContext(d, _countof(d), L"WM_CLOSE sent to the game's window from another thread (another program, or a "
                    L"plugin's thread)", ctx);
    } else {
        e = END_CLOSE_MESSAGE;
        wcscpy_s(d, L"WM_CLOSE sent on the game's own thread with no SC_CLOSE before it (a plugin, or other code in the "
                    L"game's process)");
    }
    g_ui.closeAt = now;
    g_ui.closeEnd = e;
    wcscpy_s(g_ui.closeDetail, d);
    g_ui.sysAt = g_ui.keyAt = 0;
}

static BOOL ConfirmClose(ULONGLONG now) {
    if (!g_ui.closeAt || now - g_ui.closeAt >= CONFIRM_MS) return FALSE;
    g_ui.closeAt = 0;
    Record(g_ui.closeEnd, g_ui.closeDetail);
    return TRUE;
}

// A WM_DESTROY that no close came before.
static void DestroyWithoutClose(How how) {
    if (GameExitRequested() == 1)
        Record(END_EXIT_REQUEST, L"WM_DESTROY after sMain's exit request (0x00DBD0F0) with no WM_CLOSE before it");
    else if (how == POSTED || how == SENT_FROM_OUTSIDE) {
        wchar_t d[700], ctx[320];
        OutsideContext(ctx, _countof(ctx));
        WithContext(d, _countof(d), how == POSTED ? L"WM_DESTROY posted to the game's window (another program or a "
                                                    L"plugin); the game quits on it"
                                                  : L"WM_DESTROY sent to the game's window from another thread (another "
                                                    L"program, or a plugin's thread); the game quits on it", ctx);
        Record(END_CLOSE_MESSAGE, d);
    }
    else
        Record(END_WINDOW_DESTROYED, L"WM_DESTROY of a game window on its own thread with no close before it "
                                     L"(DestroyWindow by a plugin, or by the game)");
}

// WM_DESTROY of a game window: the game posts WM_QUIT on it (0x00DF168E), whichever window it is.
static void OnDestroy(ULONGLONG now, How how, HWND w) {
    if (!g_claimed && !ConfirmClose(now)) DestroyWithoutClose(how);
    if (!g_ui.destroyLogged) {
        g_ui.destroyLogged = TRUE;
        LogLine(L"exit     WM_DESTROY of the game window 0x%p (%s)", w, HowText(how));
    }
}

// WM_QUIT reached the game's message loop, which ends on it.
static void OnQuit(ULONGLONG now, HWND w, WPARAM code) {
    if (InterlockedExchange(&g_quitSeen, 1) == 0)
        LogLine(L"exit     WM_QUIT reached the game's message loop (%s, code %lu)",
                w ? L"posted to a window" : L"from PostQuitMessage or PostThreadMessage", (unsigned long)code);
    if (g_claimed || ConfirmClose(now)) return;
    wchar_t d[700], ctx[320];
    OutsideContext(ctx, _countof(ctx));
    WithContext(d, _countof(d), w ? L"WM_QUIT posted to a window of the game (PostMessage by another program or a "
                                    L"plugin; the game's own WM_QUIT carries no window)"
                                  : L"WM_QUIT with no close or WM_DESTROY before it (PostQuitMessage or "
                                    L"PostThreadMessage, by a plugin or another program)", ctx);
    Record(END_QUIT_MESSAGE, d);
}

static const wchar_t* EndSessionWhy(LPARAM flags) {
    if (flags & ENDSESSION_CLOSEAPP) return L"a program (an installer or Windows Update, through the Restart Manager) asks it to close";
    if (flags & ENDSESSION_LOGOFF) return L"the user is signing out";
    if (flags & ENDSESSION_CRITICAL) return L"Windows is shutting down (forced)";
    return L"Windows is shutting down or restarting";
}

static void OnMessage(HWND w, UINT msg, WPARAM wp, LPARAM lp, How how) {
    ULONGLONG now = GetTickCount64();
    switch (msg) {
    case WM_SYSKEYDOWN:
        if (wp == VK_F4 && (lp & (1 << 29))) {         // bit 29: Alt is down
            g_ui.keyAt = now;
            KeySource(how, g_ui.keyFrom, _countof(g_ui.keyFrom));
        }
        break;
    case WM_SYSCOMMAND:
        if ((wp & 0xFFF0) == SC_CLOSE && GameWindow(w)) {
            g_ui.sysAt = now;
            g_ui.sysHow = how;
            g_ui.sysPoint = lp;
            if (how == SENT_HERE) g_ui.sysContext[0] = 0;
            else OutsideContext(g_ui.sysContext, _countof(g_ui.sysContext));
        }
        break;
    case WM_CLOSE:
        if (GameWindow(w)) ArmClose(now, how);
        break;
    case WM_DESTROY:
        if (GameWindow(w)) OnDestroy(now, how, w);
        break;
    case WM_QUIT:
        OnQuit(now, w, wp);
        break;
    case WM_QUERYENDSESSION:
        if (GameWindow(w) && !g_ui.askedEnd) {
            g_ui.askedEnd = TRUE;
            LogLine(L"exit     Windows asks whether the session may end: %s", EndSessionWhy(lp));
        }
        break;
    case WM_ENDSESSION:
        if (GameWindow(w) && wp) {
            wchar_t d[200];
            _snwprintf_s(d, _countof(d), _TRUNCATE, L"WM_ENDSESSION: %s", EndSessionWhy(lp));
            Record(END_SESSION, d);
        }
        break;
    }
}

// Sent messages, before the window procedure has them.
static LRESULT CALLBACK CallWndHook(int code, WPARAM wp, LPARAM lp) {
    if (code == HC_ACTION && lp) {
        const CWPSTRUCT* m = (const CWPSTRUCT*)lp;
        switch (m->message) {
        case WM_SYSKEYDOWN: case WM_SYSCOMMAND: case WM_CLOSE: case WM_DESTROY: case WM_QUERYENDSESSION: case WM_ENDSESSION:
            // InSendMessageEx, not this hook's wParam: measured on Windows 11, wParam is 0 for a send from
            // this thread and 1 for one from another (the reverse of its documentation).
            OnMessage(m->hwnd, m->message, m->wParam, m->lParam,
                      (InSendMessageEx(NULL) & (ISMEX_SEND | ISMEX_NOTIFY | ISMEX_CALLBACK)) ? SENT_FROM_OUTSIDE : SENT_HERE);
            break;
        }
    }
    return CallNextHookEx(NULL, code, wp, lp);
}

// Posted messages and input, as the game's loop takes them from its queue.
static LRESULT CALLBACK GetMsgHook(int code, WPARAM wp, LPARAM lp) {
    if (code == HC_ACTION && wp == PM_REMOVE && lp) {
        const MSG* m = (const MSG*)lp;
        switch (m->message) {
        case WM_SYSKEYDOWN: case WM_SYSCOMMAND: case WM_CLOSE: case WM_DESTROY: case WM_QUIT:
            OnMessage(m->hwnd, m->message, m->wParam, m->lParam, POSTED);
            break;
        }
    }
    return CallNextHookEx(NULL, code, wp, lp);
}

struct FindCtx { DWORD pid; HWND found; };

static BOOL CALLBACK FindGameWindowProc(HWND w, LPARAM lp) {
    FindCtx* f = (FindCtx*)lp;
    DWORD pid = 0;
    GetWindowThreadProcessId(w, &pid);
    if (pid != f->pid || !GameClassWindow(w)) return TRUE;
    f->found = w;
    return FALSE;
}

// Find the game's window and put the hooks on its thread.
static void Watch() {
    FindCtx f = {GetCurrentProcessId(), NULL};
    EnumWindows(FindGameWindowProc, (LPARAM)&f);
    HWND w = f.found;
    if (!w && g_gameWindow && IsWindow(g_gameWindow)) w = g_gameWindow;     // the window Direct3D draws to
    if (!w) return;
    DWORD tid = GetWindowThreadProcessId(w, NULL);
    g_callHook = SetWindowsHookExW(WH_CALLWNDPROC, CallWndHook, NULL, tid);
    DWORD err = GetLastError();
    if (g_callHook) {
        g_msgHook = SetWindowsHookExW(WH_GETMESSAGE, GetMsgHook, NULL, tid);
        err = GetLastError();
    }
    if (!g_callHook || !g_msgHook) {
        if (g_callHook) UnhookWindowsHookEx(g_callHook);
        g_callHook = NULL;
        g_cannotWatch = TRUE;
        LogLine(L"exit     the game window 0x%p cannot be watched (error %lu); why the game closes will not be known", w, err);
        return;
    }
    g_window = w;
    InterlockedExchange(&g_watching, 1);
    LogLine(L"exit     watching the game window 0x%p (thread %lu) for what closes the game%s", w, tid,
            ExitSitesVerified() ? L"; the game's own exit (its quit flag) is verified for this build" : L"");
}

void SessionStart() {
    g_on = IniInt(L"loader", L"exit_reason", 1) != 0;
    if (!g_on) {
        LogLine(L"exit     why the game closes is not recorded ([loader] exit_reason = 0)");
        return;
    }
    g_inputSource = (GetInputSource_t)GetProcAddress(GetModuleHandleW(L"user32.dll"), "GetCurrentInputMessageSource");
}

BOOL SessionWatchTick() {
    if (!g_on) return FALSE;
    g_ticks++;
    // The window: 20 times a second for two minutes, then every two seconds.
    if (!g_watching && !g_cannotWatch && (UptimeMs() < 120000 || g_ticks % 40 == 0)) Watch();
    // The game's own exit sets the quit flag, and its loop ends without a message.  The loop also sets
    // the flag itself as it ends on WM_QUIT, which the hooks have seen by then.  Asked only once the game
    // has made its window: its code is running then, so the byte check (done once) sees the final code.
    // Only a change from clear to set counts: in the real game (2026-09-25) the flag already read set 1.7 s
    // after start, during start-up, before the main loop ran -- and the game then ran for another minute.
    if ((g_watching || g_cannotWatch) && !g_flagSeen) {
        int flag = GameQuitFlag();
        if (flag == 0) {
            g_flagClearSeen = TRUE;
        } else if (flag == 1 && g_flagClearSeen) {
            g_flagSeen = TRUE;
            if (g_claimed || g_quitSeen) return TRUE;
            if (g_watching)
                Record(END_EXIT_MENU, L"the game set its quit flag (sApp+0x266C) with no close message before it, and "
                                      L"its main loop ended");
            else
                Record(END_UNKNOWN, L"the game's quit flag is set (its main loop ended), but its window's messages were "
                                    L"not watched, so a close message is not ruled out");
        }
    }
    return TRUE;
}

void SessionFinalize() {
    if (!g_on || g_claimed) return;
    if (ConfirmClose(GetTickCount64())) return;            // closed, and the game ended before it answered
    if (g_fatals)
        Record(END_FATAL, L"the game showed its fatal-error message, then ended with exit(1)");
    else if (g_watching)
        Record(END_SELF, L"no window message asked the game to close and its quit flag was not seen set: its own exit "
                         L"menu, or code in the game or a plugin calling exit()");
    else
        Record(END_UNKNOWN, g_cannotWatch ? L"its window could not be watched" : L"no game window was found");
}

const wchar_t* SessionEndDescription() {
    LONG e = g_end;
    return g_claimed && e > END_NONE && e < END_COUNT ? ENDS[e].text : L"";
}

const wchar_t* SessionEndText(const wchar_t* code) {
    for (int i = END_NONE + 1; i < END_COUNT; i++)
        if (code && _wcsicmp(code, ENDS[i].code) == 0) return ENDS[i].text;
    return L"";
}
