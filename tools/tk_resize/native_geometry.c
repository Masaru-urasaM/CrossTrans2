/* Private-fixture Win32 geometry through a real Tcl C-command boundary. */
#include <windows.h>

typedef struct {
    HWND hwnd;
    int x, y, width, height;
    unsigned int flags;
    DWORD error;
} Position;

typedef int (__cdecl *CommandProc)(void *, void *, int, void *const *);
typedef void *(__cdecl *CreateCommand)(void *, const char *, CommandProc,
                                      void *, void (__cdecl *)(void *));

static int __cdecl position_command(void *data, void *interp, int count, void *const *values)
{
    Position *position = (Position *)data;
    DWORD process = 0;
    (void)interp;
    (void)values;
    position->error = 0;
    GetWindowThreadProcessId(position->hwnd, &process);
    if (count != 1 || process != GetCurrentProcessId()) {
        position->error = ERROR_ACCESS_DENIED;
        return 1;
    }
    if (!SetWindowPos(position->hwnd, NULL, position->x, position->y,
                      position->width, position->height, position->flags)) {
        position->error = GetLastError();
        return 1;
    }
    return 0;
}

__declspec(dllexport) unsigned int geometry_probe_size(void)
{
    return (unsigned int)sizeof(Position);
}

__declspec(dllexport) int register_geometry_probe(void *interp, void *create_address,
                                               const char *name, Position *position)
{
    CreateCommand create = (CreateCommand)create_address;
    return create(interp, name, position_command, position, NULL) != NULL;
}
