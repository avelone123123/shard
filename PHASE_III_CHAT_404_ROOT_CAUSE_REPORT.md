# Root Cause & Acceptance Report: SHARD Private AI Chat 404 Resolution

**Date**: November 15, 2026  
**Status**: RESOLVED & ACCEPTANCE TESTED  
**Author**: Lead Systems & Security Architect  
**Target Environment**: Windows 11 (64-bit), Python 3.14  
**Dashboard URL**: `http://127.0.0.1:8000`  

---

## 1. Identification of the Stale Running Server

A diagnostic probe of listening TCP sockets and Windows CIM process instances was performed:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
Get-CimInstance Win32_Process -Filter "ProcessId = 18504"
```

### Forensic Process State Prior to Fix:
- **Port 8000 Owning Process**: PID `18504`
- **Parent Process**: PID `26040` (`shard.exe start`)
- **Process Creation Time**: `11/15/2026 8:34:36 PM`
- **Process Executable**: `C:\Python314\python.exe`
- **Full Command Line**: `"C:\Python314\python.exe" "C:\Users\avelo\AppData\Roaming\Python\Python314\Scripts\shard.exe" start`
- **Imported `shard_engine` Location**: `C:\Users\avelo\Desktop\my projects\shard\shard_engine\__init__.py`
- **Active Routes in Memory**: 108 routes. Registered chat routes were limited to:
  `['/v1/chat/completions', '/api/server/shared-chat', '/api/server/shared-chat/{chat_id}']`

### Root Cause:
The server process was launched at 8:34 PM, prior to the implementation of the Phase III private chat routes (`/api/server/chats` and `/api/server/chats/{chat_id}`). Because Python modules are loaded into memory at process boot time and Uvicorn was running without auto-reload, the live server retained the pre-Phase III route table. When the newly updated dashboard made calls to `/api/server/chats`, FastAPI returned `404 Not Found`.

---

## 2. Server Teardown & Version Synchronization

1. **Targeted Process Termination**:
   - Executed `shard stop` (`python -m shard_engine.cli stop`).
   - Verified that PID `18504` and its parent PID `26040` exited cleanly.
   - Verified that TCP port 8000 was fully released.
2. **Phase III Backend Startup**:
   - Launched the updated backend directly from the current working tree: `python -m shard_engine.main`.
   - Verified new PID listening on port 8000.
   - Queried `http://127.0.0.1:8000/openapi.json`: confirmed 115 registered routes, with all private chat endpoints present:
     - `GET /api/server/chats`
     - `POST /api/server/chats`
     - `GET /api/server/chats/{chat_id}`
     - `DELETE /api/server/chats/{chat_id}`

---

## 3. Implementation of Dashboard / Backend Compatibility Detection

To prevent silent failures and false "ready" states when connecting to an outdated server:
1. In `dashboard.html` and `shard_engine/dashboard.html`:
   - Updated `srvLoadPrivateChats()`: if `/api/server/chats` returns HTTP 404, it immediately sets `srvBackendOutdated = true`, disables the chat composer input and send button, and displays an alert banner:
     > **Backend устарел. Перезапустите SHARD, чтобы загрузить актуальный API.**
   - Updated `srvSendSharedChat()`: if any chat request encounters an HTTP 404, it halts submission, shows a toast notification, and renders the warning in the chat container.
2. **Byte Parity**: Both `dashboard.html` and `shard_engine/dashboard.html` were maintained at 100% byte-for-byte equality (343,231 bytes).

---

## 4. Participant Isolation & API Verification

The participant derivation logic in `shard_engine/server.py` was enhanced to support explicit session identifiers (`X-Session-Token` and `X-Participant-Id`):
- Tested with **Alice** (`X-Session-Token: alice-session-123` -> PID `p_f806f30b542f58a9`).
- Tested with **Bob** (`X-Session-Token: bob-session-456` -> PID `p_63291efbde853bef`).
- Tested with **Owner** (`localhost` loopback -> PID `owner`).

### Verified API Invariants:
1. **Creation**: Alice creates a chat session via `POST /api/server/chats` (`create_only: true`). Chat `pchat-c8249d4bd5` is created on disk in `private_chats/p_f806f30b542f58a9.json`.
2. **Listing**: Alice queries `GET /api/server/chats` -> receives 1 chat. Bob queries `GET /api/server/chats` -> receives 0 chats.
3. **Cross-Participant Read Denial**: Bob requests `GET /api/server/chats/pchat-c8249d4bd5` -> returns `HTTP 404 Not Found`.
4. **Cross-Participant Delete Denial**: Bob requests `DELETE /api/server/chats/pchat-c8249d4bd5` -> returns `HTTP 404 Not Found`.
5. **Legitimate Deletion**: Alice requests `DELETE /api/server/chats/pchat-c8249d4bd5` -> returns `HTTP 200 OK`, purging the chat.
6. **Persistence**: Server restarted; owner session `pchat-4cbb9750bb` was reloaded from disk with title and history intact.

---

## 5. CI Workflow & Windows Encoding Fixes

1. **Workflow Update (`.github/workflows/tests.yml`)**:
   - Added `tests_phase_iii_unified_hosting.py` as an explicit step.
   - Converted multi-command scripts into isolated GitHub Actions steps to guarantee that any single failure immediately halts the job and prevents masking.
2. **Console Encoding Safety (`shard_engine/lan.py`)**:
   - Introduced `_safe_print()` helper handling `UnicodeEncodeError` when emitting Cyrillic log messages on Windows terminals with CP1252 or ASCII encodings.
   - Replaced unhandled `print` calls in heartbeat and member recovery routines.

---

## 6. Real Browser Acceptance Test (Playwright)

Automated end-to-end verification was executed against the live server using Playwright:
- **Browser**: Chromium Headless (1440x900)
- **Flow**:
  1. Navigated to `http://127.0.0.1:8000`.
  2. Clicked `#navServerBtn` ("Server SHARD").
  3. Clicked "Мои AI-диалоги" subtab.
  4. Clicked "+ Новый" dialog button.
  5. Verified dropdown selected newly created session `pchat-7826922a80`.
  6. Verified chat container displayed: *"Диалог пуст. Отправьте первый запрос модели."*
  7. Confirmed that `#srvChatOutdatedWarn` was NOT visible.
  8. Intercepted 5 network requests to `/api/server/chats`: **0 errors, all returned HTTP 200 OK**.
- **Screenshot Artifact**: `scratch/browser_chat_verified.png`.

---

## 7. Test Suite Status

```
Ran 180 tests in 41.228s
OK (0 Failures, 0 Errors)
```
