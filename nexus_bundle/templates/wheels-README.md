# 既有 Nexus 伺服器的 wheel 更新封裝

需要 Linux、Bash、sha256sum 與 Docker Engine。
目標 PyPI 託管儲存庫必須已存在，且允許上傳。
Nexus 必須事先完成設定，包括在必要時接受 EULA。
此更新只會上傳套件，不會啟動、重新設定或升級 Nexus，也不會刪除舊版本。

```bash
./verify-bundle.sh
./upload-wheels.sh --url http://localhost:8081 --repository pypi-offline --username admin
```

請在提示時輸入密碼；若需自動化，可用安全方式匯出 `NEXUS_PASSWORD`。
也可透過環境變數設定 `NEXUS_URL`、`NEXUS_REPOSITORY` 與 `NEXUS_USERNAME`。
請使用 Nexus 基礎 URL，若有設定應用程式路徑（context path），也須一併包含，
不要使用儲存庫的 URL，也不要在 URL 中加入登入資訊。
HTTPS 會執行標準憑證驗證，請為伺服器設定受信任的憑證。

腳本預設使用 Linux 主機網路，因此 localhost 指向執行 Docker 的主機。
若要透過 Docker 網路，以容器名稱連線至 Nexus，可執行：

```bash
./upload-wheels.sh --url http://nexus:8081 --network your-existing-network
```

上傳工具映像檔名稱記錄於 `uploader-image.txt`。
小型更新封裝需要先從原始部署封裝載入該映像檔。
若使用其他相容的工具映像檔，必須包含 Python 與 Twine 7 以上版本，
並用 `--image IMAGE` 或 `UPLOADER_IMAGE` 指定。
所需的容器架構記錄於 `container-platform.txt`。
也可在建置時加上 `--include-uploader`：腳本會自動載入 `images/uploader.tar`，
無須連線至映像檔登錄服務。

封裝包含收集到的全部套件與相依套件，不限於 Nexus 中尚未存在的套件。
遠端校驗值相符時會略過上傳；相同檔名但內容不同時則回報失敗。
上傳帳號需要儲存庫上傳與瀏覽／讀取權限，以透過 assets API 比對校驗值。
具備這些權限的帳號不必是管理員。
上傳失敗時，修正問題後可再次執行；先前已成功上傳的套件會被略過。
密碼不會儲存於壓縮檔中。

`requirements.txt` 列出解析後的版本；`requirements.input.txt` 保留原始固定版本設定。
wheel 適用的 Python／作業系統／CPU 環境記錄於 `manifest.json`。
校驗值可偵測檔案損毀，無法驗證傳送者身分，請透過可信任的管道傳送封裝。
請在修改部署檔案之前完成校驗。
