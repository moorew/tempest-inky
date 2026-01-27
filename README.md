## Configuration
**You must add your Tempest credentials for the dashboard to work.**

1.  **Station ID:** Find this on the Tempest website (Settings > Stations > public-url-id).
2.  **API Token:** Generate a Personal Use Token at Tempest Settings > Data Authorizations.

### Option A: Windows Installer Users
If you installed the app using the `.exe` installer:
1.  Navigate to your **User Home Folder** (e.g., `C:\Users\YourName\`).
2.  Create a file named `secrets.py`.
3.  Add your keys:
    ```python
    STATION_ID = "12345"
    TOKEN = "your-long-token-string"
    ```
4.  Restart the app.

### Option B: Raspberry Pi / Developers
If you are running the source code directly (or on a Pi):
1.  Create the `secrets.py` file inside the project folder:
    ```bash
    nano secrets.py
    ```
2.  Paste your details:
    ```python
    STATION_ID = "12345"
    TOKEN = "your-long-token-string"
    ```
3.  Save and exit. The script will look here first.