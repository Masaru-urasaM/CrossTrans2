#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <commctrl.h>
#include <stdint.h>
#include <string.h>

#define EXPORT __declspec(dllexport)
#define CAPACITY 8192
#define WINDOWS 256

typedef struct {
    uint64_t sequence, begin, end, hwnd, surface, role, message, flags, active;
    int64_t x, y, width, height, proposed_x, proposed_y, proposed_width, proposed_height;
    int64_t pointer_x, pointer_y;
} Trace;
typedef struct { HWND hwnd; unsigned surface, role; int installed; } Window;
static Trace rows[CAPACITY];
static Window windows[WINDOWS];
static uint64_t written, sequence, active;
static unsigned window_count;
static unsigned inflight;
static DWORD owner;
static int recording;

static uint64_t ticks(void) {
    LARGE_INTEGER value;
    QueryPerformanceCounter(&value);
    return (uint64_t)value.QuadPart;
}
static int selected(UINT message) {
    return message == WM_ENTERSIZEMOVE || message == WM_EXITSIZEMOVE
        || message == WM_SIZING || message == WM_MOVING || message == WM_SIZE
        || message == WM_WINDOWPOSCHANGED || message == WM_PAINT;
}
static LRESULT CALLBACK observe(HWND hwnd, UINT message, WPARAM wp, LPARAM lp,
                                UINT_PTR identity, DWORD_PTR data) {
    Window *window = (Window *)data;
    Trace row;
    int capture = recording && selected(message);
    LRESULT result;
    inflight++;
    if (capture) {
        memset(&row, 0, sizeof(row));
        row.sequence = ++sequence;
        row.begin = ticks();
        row.hwnd = (uint64_t)(uintptr_t)hwnd;
        row.surface = window->surface;
        row.role = window->role;
        row.message = message;
        if (window->role == 1 && message == WM_ENTERSIZEMOVE)
            active |= (uint64_t)1 << window->surface;
        row.active = active;
    }
    result = DefSubclassProc(hwnd, message, wp, lp);
    if (capture && recording) {
        RECT rectangle;
        row.end = ticks();
        if (GetWindowRect(hwnd, &rectangle)) {
            row.x = rectangle.left; row.y = rectangle.top;
            row.width = rectangle.right - rectangle.left;
            row.height = rectangle.bottom - rectangle.top;
        }
        if (message == WM_SIZING || message == WM_MOVING) {
            RECT *proposed = (RECT *)lp;
            POINT point;
            row.flags = (uint64_t)wp;
            row.proposed_x = proposed->left; row.proposed_y = proposed->top;
            row.proposed_width = proposed->right - proposed->left;
            row.proposed_height = proposed->bottom - proposed->top;
            if (GetCursorPos(&point)) {
                row.pointer_x = point.x; row.pointer_y = point.y;
            }
        } else if (message == WM_WINDOWPOSCHANGED) {
            row.flags = ((WINDOWPOS *)lp)->flags;
        }
        rows[written % CAPACITY] = row;
        written++;
    }
    if (window->role == 1 && message == WM_EXITSIZEMOVE)
        active &= ~((uint64_t)1 << window->surface);
    if (message == WM_NCDESTROY) {
        RemoveWindowSubclass(hwnd, observe, identity);
        window->installed = 0;
    }
    inflight--;
    return result;
}
EXPORT unsigned trace_version(void) { return 1; }
EXPORT unsigned trace_row_size(void) { return sizeof(Trace); }
EXPORT int trace_open(void) {
    unsigned i;
    if (inflight) return 0;
    for (i = 0; i < window_count; i++)
        if (windows[i].installed && IsWindow(windows[i].hwnd)) return 0;
    memset(windows, 0, sizeof(windows));
    window_count = 0;
    owner = GetCurrentThreadId();
    written = sequence = active = 0;
    recording = 0;
    return 1;
}
EXPORT uint64_t trace_frequency(void) {
    LARGE_INTEGER value;
    QueryPerformanceFrequency(&value);
    return (uint64_t)value.QuadPart;
}
EXPORT uint64_t trace_clock(void) { return ticks(); }
EXPORT int trace_attach(HWND hwnd, unsigned surface, unsigned role) {
    DWORD process, thread = GetWindowThreadProcessId(hwnd, &process);
    Window *window;
    unsigned i;
    if (!owner) owner = GetCurrentThreadId();
    if (thread != owner || GetCurrentThreadId() != owner
            || process != GetCurrentProcessId() || surface > 1 || role < 1 || role > 3)
        return 0;
    for (i = 0; i < window_count; i++)
        if (windows[i].installed && windows[i].hwnd == hwnd) return 1;
    if (window_count >= WINDOWS) return 0;
    window = &windows[window_count];
    window->hwnd = hwnd; window->surface = surface; window->role = role;
    if (!SetWindowSubclass(hwnd, observe, (UINT_PTR)window, (DWORD_PTR)window)) return 0;
    window->installed = 1;
    window_count++;
    recording = 1;
    return 1;
}
EXPORT void trace_pause(void) { recording = 0; }
EXPORT uint64_t trace_total(void) { return written; }
EXPORT unsigned trace_count(void) {
    return written < CAPACITY ? (unsigned)written : CAPACITY;
}
EXPORT int trace_read(unsigned index, Trace *output) {
    uint64_t oldest = written > CAPACITY ? written - CAPACITY : 0;
    if (GetCurrentThreadId() != owner || index >= trace_count()) return 0;
    *output = rows[(oldest + index) % CAPACITY];
    return 1;
}
EXPORT unsigned trace_close(void) {
    unsigned i, failed = 0;
    recording = 0;
    for (i = 0; i < window_count; i++) {
        Window *window = &windows[i];
        if (window->installed && IsWindow(window->hwnd)
                && !RemoveWindowSubclass(window->hwnd, observe, (UINT_PTR)window)) {
            failed++;
        } else {
            window->installed = 0;
        }
    }
    return failed;
}
