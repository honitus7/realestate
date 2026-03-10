# Folder (workspace) access – test matrix

Folders are implemented as **workspaces**. Access is granted via the `workspace_access` table. Use this matrix to verify behavior when testing with multiple users.

## Roles and permissions

| Role / context | Can create/rename/delete folder | Can move panoramas into folder | Can grant/revoke folder access | Can edit panoramas/plots/markers in folder |
|----------------|----------------------------------|--------------------------------|---------------------------------|-------------------------------------------|
| **Owner** (user_id = workspace.user_id) | Yes | Yes | Yes | Yes |
| **User with "client" folder access** | No | No | No | Yes |
| **User with "viewer" folder access** | No | No | No | No (view only) |
| **User with no folder access** | N/A | N/A | N/A | No (folder and its panoramas are invisible) |

## Suggested test matrix

### 1. Owner

- Create a folder (workspace), rename it, delete it.
- Move panoramas between “Unfiled” and the folder (dashboard).
- Open “Manage folder access” and grant **client** or **viewer** to another user.
- Confirm the owner sees all panoramas in owned folders in the dashboard and in `GET /api/panoramas`.
- In customer 3D, open a panorama in that folder and confirm the side menu lists only 360° panoramas from the **same folder**.

### 2. User with “client” folder access

- Confirm they see the shared folder in the dashboard (and in `GET /api/workspaces`).
- Confirm they see panoramas in that folder in `GET /api/panoramas`.
- Confirm they can edit plots/markers in those panoramas (if the app allows).
- Confirm they **cannot** manage the folder (rename, delete, grant/revoke access).
- In customer 3D, open a panorama in that folder and confirm the side menu shows only 360° panos from the same folder.

### 3. User with “viewer” folder access

- Confirm they see the shared folder and its panoramas.
- Confirm they **cannot** edit; view-only.
- Confirm they cannot manage folder access.

### 4. User with no folder access

- Confirm they do **not** see that folder in the dashboard or in `GET /api/workspaces`.
- Confirm they do not see that folder’s panoramas in `GET /api/panoramas`.
- Direct link to a panorama in that folder should 403 or 404 depending on implementation (currently access is enforced via `get_panorama_with_access`).

### 5. Multiple folders

- User A owns Folder F1 and has **client** access to Folder F2 (owned by User B).
- Confirm User A sees both F1 and F2 in the dashboard with correct panorama counts.
- Confirm no cross-folder leakage (panoramas from F2 do not appear under F1 and vice versa).

### 6. Customer 3D side menu (after Item 1)

- Open a panorama that belongs to folder F.
- Confirm the 360° panorama side menu lists only panoramas in the **same folder** F (plus current and any linked from plots/markers).
- Log in as a user who has access only to folder F and confirm they only see F’s panoramas in the index.

## Implementation notes

- **Folder** = one row in `public.workspaces`; content = panoramas with `panoramas.workspace_id` set.
- **Access** = `public.workspace_access` (workspace_id, user_id, access_type in `('client','viewer')`).
- Panorama visibility: `db.get_panorama_with_access()` and `db.list_panoramas_for_user()` enforce owner, direct share, and folder share.
- Grant/revoke: `app.py` `grant_workspace_access`, `revoke_workspace_access`; only owner or org admin can manage.

## No code changes required for testing

This document is for manual (or later automated) testing. No application code changes are required for item 7; add integration tests later if desired (e.g. call API as different users and assert on `list_panoramas_for_user` and `get_panorama_with_access` for various workspace_id scenarios).
