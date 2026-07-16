# 這個專案怎麼跑起來？新手版說明

先講重點：你不需要全部看懂，跟著步驟一步一步做，卡住再回來看「卡住怎麼辦」那段就好。

## 第一步：把資料夾打開

解壓縮之後，打開 VSCode，點左上角「File」→「Open Folder」，選剛剛解壓縮出來的 `hybrid-rag-project` 資料夾。

順手裝兩個 VSCode 的擴充套件（左邊工具列有個像積木的圖案，點進去搜尋安裝）：
- **Python**（Microsoft 出的，讓 VSCode 看得懂 Python）
- **Pylance**（讓 VSCode 提示錯誤更聰明）

這兩個裝好基本上就夠用了，不裝也能跑，只是比較沒有提示。

## 第二步：把「虛擬環境」建起來

先解釋一下這是什麼：你電腦裡可能已經裝了很多 Python 套件，如果全部混在一起，很容易某天裝了新東西把舊的東西弄壞。「虛擬環境」就是幫這個專案獨立開一個乾淨的小房間，裝的東西只有這個專案在用，不會互相干擾。

打開 VSCode 下方的終端機（快捷鍵是按住 Ctrl 再按那個 `` ` `` 鍵，就在數字鍵 1 左邊），貼上以下指令：

**如果你是 Mac 或 Linux：**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**如果你是 Windows：**
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

跑完最後一行會裝一堆東西，跑久一點是正常的，等它跑完就好。

裝完之後，VSCode 右下角應該會跳出提示問你要不要選這個 Python，選「是」，或是自己點右下角切換到 `venv` 那個版本。

**怎麼確認有成功？** 終端機最前面如果出現 `(venv)` 這幾個字，就代表你現在在這個乾淨的小房間裡了，之後裝東西都不會影響到你電腦其他地方。

## 第三步：申請一組免費的 API 金鑰

這個專案要呼叫 AI 模型才能運作，所以需要一組「金鑰」（可以想成是你的專屬密碼，用來證明這個請求是你授權的）。

這裡用的是 **Google Gemini 的免費額度**，不用信用卡、不用付錢，申請完直接能用：

1. 打開 https://aistudio.google.com/apikey
2. 用你的 Google 帳號登入
3. 點「Create API key」，選一個專案（沒有的話它會幫你建一個新的）
4. 複製產生出來的那串金鑰

免費額度目前大約是一天 1000 多次請求，對你這個練習/比賽用途完全夠用。要注意兩件事：
- 免費額度的用量 Google 可能會拿去改善他們的模型，如果你的資料是機密的（比如真實客戶財報），正式上線建議改用付費版
- 免費額度是「整個專案共用」，不是每組金鑰各自獨立，所以不用一直重新申請新的金鑰

拿到金鑰後，先複製一份範例檔案：
```bash
cp .env.example .env
```
（Windows 上如果指令不能用，改打 `copy .env.example .env`）

複製完之後，在 VSCode 左邊檔案列表找到新出現的 `.env` 檔案，點開它，把 `your_key_here` 換成你剛剛複製的金鑰，貼上去就好，不要留空格。

## 第四步：準備一份簡報當測試資料

隨便找一份法說會或財報的 PDF，丟到專案最外層那層資料夾（跟 `README.md` 同一層），然後把檔名改成 `sample.pdf`。

## 第五步：一個一個功能分開測試

程式拆成好幾支小檔案，建議一支一支跑，確認每個都沒問題，之後串起來才不會不知道哪裡壞掉。

在終端機打：
```bash
cd src
```

然後照順序執行（每次跑一行，按 Enter，等它跑完再跑下一行）：

```bash
python preprocess_pdf.py
```
這支是把 PDF 每一頁拆成一張一張圖片。

```bash
python vlm_parse.py
```
這支是把圖片丟給 AI 看，讓它讀懂圖表上的數字。這支會真的呼叫 API，會花一點點費用，不用太擔心，測試用量很小。

```bash
python vector_rag.py
```
這支在測試「找相關文字」的功能，不用真實資料也能跑起來看效果。

```bash
python graph_rag.py
```
這支在測試「精準算數字」的功能，比如季增率、年增率，這部分是直接用公式算，不會讓 AI 用猜的。

```bash
python agent_router.py
```
這支把上面幾個功能串起來，測試「AI 自己判斷該用哪個方法回答問題」。

```bash
python report_generator.py
```
這支是把分析結果整理成一份 Word 文件。

**懶得打指令的話**，也可以直接在 VSCode 裡打開某支檔案，在程式碼上按右鍵，選「Run Python File」，效果一樣。

## 第六步：打開網頁介面，實際跟它聊天

回到最外層資料夾（打 `cd ..` 回上一層），執行：
```bash
streamlit run src/app.py
```

它會自動幫你開一個瀏覽器分頁，網址通常是 `http://localhost:8501`，這就是你可以現場展示的聊天介面。

## 這個資料夾裡面各個檔案在幹嘛

```
hybrid-rag-project/
├── src/
│   ├── preprocess_pdf.py    → 把 PDF 拆成一張張圖片
│   ├── vlm_parse.py         → 讓 AI 讀懂圖片裡的圖表和數字
│   ├── vector_rag.py        → 負責「找出相關的文字說明」
│   ├── graph_rag.py         → 負責「精準計算數字變化」
│   ├── agent_router.py      → 負責「判斷問題該用哪個方法回答」
│   ├── report_generator.py  → 把結果整理成 Word 報告
│   └── app.py                → 網頁聊天介面
├── pages/        → PDF 拆出來的圖片會自動存在這裡
├── vector_db/    → 資料庫檔案，不用管它
├── outputs/      → 產生出來的報告和結果會放這裡
├── requirements.txt   → 記錄這個專案需要裝哪些套件
├── .env.example       → 金鑰範例檔（記得複製成 .env 再填金鑰）
└── .gitignore         → 告訴 Git 哪些檔案不用管，新手可以先忽略這個
```

## 之後要換成比賽方提供的 EAP 平台

現在專案裡用的是一般市面上找得到的工具先讓你看懂邏輯怎麼跑。等你拿到 EAP 平台的技術文件之後，去每支檔案裡搜尋 `TODO`，那些地方就是需要換成 EAP 提供的功能的地方，其他邏輯都不用改。

## 卡住的時候，先看這裡

**跑起來說 `AuthenticationError`**
代表金鑰沒填對，回去檢查 `.env` 裡面那串金鑰有沒有貼對、有沒有多餘的空格。

**跑起來說 `ModuleNotFoundError`，說找不到某個套件**
先看終端機最前面有沒有 `(venv)` 這幾個字，沒有的話代表虛擬環境沒有啟動，回到第二步重新執行 `source venv/bin/activate`（Windows 是 `venv\Scripts\activate`）。

**跟資料庫（ChromaDB）有關的報錯**
通常是資料夾權限問題，把 `vector_db` 這個資料夾整個刪掉，重新跑一次程式，它會自動重建。

**完全不知道錯誤訊息在講什麼**
直接把錯誤訊息整段複製貼給我，我幫你看。

## 部署到 Streamlit Community Cloud（讓別人也能用網址連進來）

### 第一步：把專案放到 GitHub
1. 去 https://github.com 註冊帳號（如果還沒有）
2. 建立一個新的 repository（右上角 + 號 → New repository），設成 Public 或 Private 都可以
3. 在 VSCode 終端機（在 `hybrid-rag-project` 最外層）執行：
```powershell
git init
git add .
git commit -m "first version"
git branch -M main
git remote add origin 你的repo網址
git push -u origin main
```
`.env` 跟 `vector_db` 這些檔案因為 `.gitignore` 已經設定好，不會被上傳，金鑰不會外洩。

### 第二步：連接 Streamlit Cloud
1. 打開 https://share.streamlit.io，用 GitHub 帳號登入
2. 點 "New app"，選擇你剛剛建立的 repository
3. Main file path 填：`src/app.py`
4. 點 "Advanced settings" 展開，在 "Secrets" 欄位貼上（格式是 TOML）：
```toml
GEMINI_API_KEY = "你的金鑰"
```
5. 點 "Deploy"，等個幾分鐘它會自動安裝 `requirements.txt` 裡的套件並啟動

### 部署後要注意的事
- **資料不會永久保存**：雲端環境重啟或你更新程式碼時，之前上傳的資料會被清空，要重新用網頁上的「上傳新資料」功能匯入一次
- **額度是共用的**：所有連進這個網址的人都共用同一組 Gemini 免費額度，人多的時候容易撞到「每分鐘/每天」的限制
- 更新程式碼後，把新的變動 `git push` 上去，Streamlit Cloud 會自動重新部署
