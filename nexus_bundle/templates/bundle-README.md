# Nexus 離線部署封裝

必要環境：Linux、Docker Engine、Docker Compose 外掛、Bash、sha256sum，
以及足夠的記憶體與磁碟空間，建議至少預留 4 GB RAM 與 10 GB 可用磁碟空間。
部署主機不需要 Python 或網際網路連線。
映像檔適用平台記錄於 `manifest.json`，支援的 wheel 環境則由內附的 Python 映像檔決定。

```bash
./verify-bundle.sh
./start.sh
./bootstrap-nexus.sh
./upload-wheels.sh
./verify-install.sh
```

初始化腳本會提示輸入新的或既有的管理員密碼；上傳腳本會另外提示輸入密碼。
若需自動化，請在執行腳本前，以安全方式設定並匯出 `NEXUS_PASSWORD`。
密碼不會儲存於封裝中。初始化後會啟用匿名套件讀取；設定與上傳需要管理員驗證。
既有儲存庫必須為 PyPI 託管類型；既有 wheel 僅在校驗值相符時才會略過。

Nexus 預設監聽 `127.0.0.1:8081`。
可修改 `.env` 中的 `NEXUS_BIND`，讓其他主機連線。
若要透過不可信任的網路傳送登入資訊，請先在反向代理設定 HTTPS。
從儲存庫安裝套件的方式如下：

```bash
python -m pip install --index-url http://localhost:8081/repository/pypi-offline/simple/ \
  --trusted-host localhost -r requirements.txt
```

若儲存庫名稱不同，請替換指令中的名稱。使用 HTTPS 時，請移除 `--trusted-host`。
`verify-install.sh` 使用內附的 Python 直譯器測試；
含原生程式碼的相依套件仍需在應用程式的目標系統上另外驗證。

Nexus 資料會持續保存在 Compose 的 `nexus-data` 磁碟區。
`docker compose down` 會停止服務並保留資料。
除非確定要刪除所有儲存庫，否則請勿執行 `docker compose down -v`。
請在此解壓縮目錄中執行指令；目錄名稱會影響 Compose 專案與磁碟區名稱。
若移動或重新命名目錄，請使用一致的 `COMPOSE_PROJECT_NAME`。

啟動失敗時，可執行 `docker compose logs nexus` 查看記錄。
上傳失敗時，修正問題後再次執行 `upload-wheels.sh`；內容相符的套件會被略過。
初始化腳本可重複執行，但需要管理員登入資訊，且會啟用匿名讀取。
`SHA256SUMS` 可偵測意外損毀，無法驗證傳送者身分。
可編輯的 `.env` 不納入校驗；`.env.example` 記錄預設設定。
修改其他部署檔案會改變其校驗值。

Nexus Community Edition 必須先接受終端使用者授權合約（EULA），才能上傳或下載套件。
初始化腳本會顯示伺服器提供的聲明，並要求明確同意。
若需無人值守部署，請先閱讀 [Sonatype CE EULA](https://links.sonatype.com/products/nxrm/ce-eula)，
再執行 `./bootstrap-nexus.sh --accept-eula` 授權接受合約。
整合測試同樣需要 `--accept-nexus-eula`；建立封裝時不會自動接受任何 EULA。
