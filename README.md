# torrent-watchdog

Веб-сервис для отслеживания обновлений торрент-раздач. Сервис сохраняет текущий `info_hash`, проверяет источник по расписанию и показывает найденные обновления. Если включён `auto_update`, он заменяет торрент в удалённом qBittorrent: ставит старый торрент на паузу, удаляет его из qBittorrent с `deleteFiles=false`, добавляет новый `.torrent` в тот же `save_path`, восстанавливает категорию и теги, запускает recheck и затем resume, если это включено.

Версия: `0.1.0`.

## Запуск

```bash
cp .env.example .env
docker compose up -d --build
```

Откройте веб-интерфейс:

```text
http://SERVER_IP:8096
```

По умолчанию включена Basic Auth:

```text
admin / change_me
```

Смените пароль в `.env` перед постоянным использованием.

## Настройка .env

Основные переменные:

```env
APP_PORT=8096
TZ=Asia/Yekaterinburg
DATABASE_URL=sqlite:////data/app.db

QB_HOST=http://192.168.0.220:8090
QB_USERNAME=admin
QB_PASSWORD=adminadmin
QB_VERIFY_TLS=false

CHECK_HOUR=4
CHECK_MINUTE=0

DEFAULT_AUTO_UPDATE=false
DEFAULT_RECHECK_AFTER_ADD=true
DEFAULT_START_AFTER_RECHECK=true
DEFAULT_ADD_PAUSED=true

APP_AUTH_ENABLED=true
APP_AUTH_USERNAME=admin
APP_AUTH_PASSWORD=change_me

RUTRACKER_ENABLED=true
RUTRACKER_COOKIE=
FLARESOLVER_ADDRESS=
FLARESOLVER_PORT=8191
```

Если меняете `APP_PORT`, docker-compose пробросит тот же порт наружу и внутрь контейнера.

## qBittorrent на другой ВМ

qBittorrent не запускается внутри `docker-compose.yml` этого проекта. Он должен быть доступен по HTTP через Web API на другой ВМ или хосте.

Не используйте `localhost` или `127.0.0.1` в `QB_HOST`: внутри Docker-контейнера это будет сам контейнер `torrent-watchdog`, а не ВМ с qBittorrent.

Настройка qBittorrent на другой ВМ:

1. В qBittorrent включить Web UI.
2. Убедиться, что Web UI доступен по IP ВМ, а не только на `127.0.0.1`.
3. Проверить порт, например `8090`.
4. На ВМ с qBittorrent открыть порт в firewall.
5. В `.env` сервиса `torrent-watchdog` указать:

```env
QB_HOST=http://192.168.0.220:8090
QB_USERNAME=ваш_логин
QB_PASSWORD=ваш_пароль
```

6. Проверить доступность с сервера:

```bash
curl -I http://192.168.0.220:8090
```

7. Если сервис запущен в Docker, проверить из контейнера:

```bash
docker exec -it torrent-watchdog sh
wget -S -O- http://192.168.0.220:8090
```

Пользователь из `QB_USERNAME` должен иметь право добавлять, удалять, ставить на паузу, запускать и recheck торренты.

Для qBittorrent 5.x сервис автоматически использует новые endpoints `stop/start`, если старые `pause/resume` недоступны.

Проверить подключение можно на странице `Настройки`. Там же можно добавить несколько qBittorrent-клиентов, выбрать основной клиент и затем выбирать нужный клиент при добавлении раздачи.

При первом запуске создаётся клиент `Основной` из переменных `QB_HOST`, `QB_USERNAME`, `QB_PASSWORD`. Уже добавленные раздачи автоматически привязываются к нему.

Категории qBittorrent автоматически подгружаются на странице добавления раздачи и доступны через API:

```text
GET /api/qbittorrent/categories
GET /api/qbittorrent/clients
```

Если qBittorrent недоступен, приложение всё равно запускается. `/health` покажет статус qBittorrent, а операции добавления или обновления завершатся ошибкой в интерфейсе и в `check_events`.

## Добавление первой раздачи

Откройте `Добавить` и заполните:

- ссылку на `.torrent`, magnet или страницу RuTracker;
- путь загрузки `save_path`;
- категорию и теги при необходимости;
- параметры `auto_update`, `recheck_after_add`, `start_after_recheck`, `add_paused`.

После добавления сервис скачает или разберёт источник, рассчитает `info_hash`, добавит торрент в qBittorrent и создаст первую запись истории версий.

## Проверка обновлений

Планировщик запускается ежедневно в `CHECK_HOUR:CHECK_MINUTE` с timezone из `TZ`. Ручная проверка доступна кнопкой `Проверить сейчас` на главной странице и в карточке раздачи.

Если `info_hash` не изменился, статус остаётся `active`. Если изменился, создаётся новая версия и статус становится `update_available`.

## Auto update

Если у раздачи включён `auto_update`, найденная новая версия применяется автоматически:

1. старый торрент ставится на паузу;
2. старый торрент удаляется из qBittorrent с `deleteFiles=false`;
3. новый `.torrent` добавляется в тот же `save_path`;
4. назначаются прежние категория и теги;
5. применяется выбранный режим обновления;
6. торрент запускается, если включено.

Доступны два режима:

- `Скачивать только новые файлы` - режим для медиатеки. Сервис сравнивает старый и новый `.torrent`, старые файлы в новом torrent помечает как `не скачивать`, новые оставляет выбранными. `recheck` не запускается, чтобы qBittorrent не перекачивал файлы, в которые уже записаны теги.
- `Полная замена с проверкой файлов` - классический режим: добавить новый torrent, запустить recheck и восстановить оригинальные данные раздачи.

## Почему файлы не удаляются

Проект никогда не удаляет файлы раздачи с диска. Вызов qBittorrent delete API централизован в `app/services/qbittorrent_client.py`, и параметр `delete_files` по умолчанию равен `False`. Обновление и rollback явно вызывают удаление с `delete_files=False`.

Удаление из отслеживания удаляет только запись из базы. Файлы на диске и данные qBittorrent не удаляются.

## RuTracker

Для ссылки вида:

```text
https://rutracker.org/forum/viewtopic.php?t=123456
```

resolver берёт topic id и скачивает:

```text
https://rutracker.org/forum/dl.php?t=123456
```

Для скачивания нужен `RUTRACKER_COOKIE`. Вставьте cookie авторизованной сессии в `.env`:

```env
RUTRACKER_COOKIE=bb_session=...
```

Cookie также можно вставить на странице `Настройки`. Значение из страницы настроек используется в первую очередь, `.env` остаётся fallback.

### FlareSolverr

Если RuTracker показывает Cloudflare-проверку, укажите в `Настройки` адрес и порт работающего FlareSolverr. Например, при запуске отдельного контейнера на сервере: `http://192.168.1.10` и `8191`. Сервис получит через FlareSolverr актуальные cookies и User-Agent, затем скачает `.torrent` с ними. Поля можно задать и в `.env`:

```env
FLARESOLVER_ADDRESS=http://192.168.1.10
FLARESOLVER_PORT=8191
```

Не публикуйте FlareSolverr в интернете — он должен быть доступен только torrent-watchdog.

Важно: одного `cf_clearance` недостаточно. Это cookie Cloudflare, а не авторизация RuTracker. В строке должны быть авторизационные cookie RuTracker, например `bb_session`, `bb_t` или `bb_data`.

Секреты и passkey в URL маскируются в логах.

Если RuTracker временно не отвечает или возвращает не `.torrent`, resolver повторяет запрос до успешного ответа. Настройки:

```env
RUTRACKER_RETRY_DELAY_SECONDS=10
RUTRACKER_MAX_ATTEMPTS=3
```

`RUTRACKER_MAX_ATTEMPTS=0` означает повторять без ограничения, но для обычной эксплуатации лучше оставлять конечное число попыток. Иначе при DNS/сетевой проблеме ручная или плановая проверка может не завершиться. Если cookie не задан или resolver отключён, ошибка возвращается сразу, потому что повтор не сможет исправить конфигурацию.

## Восстановление после ошибки

Откройте карточку раздачи. Там отображается `last_error` и история событий. Можно:

- нажать `Проверить сейчас`;
- вручную применить найденную версию;
- применить одну из сохранённых старых версий из истории.

Rollback тоже удаляет текущий торрент из qBittorrent с `deleteFiles=false`, добавляет сохранённый старый `.torrent`, запускает recheck и обновляет текущий `info_hash`.

## Логи

События проверок доступны на странице `Логи`. Логи контейнера:

```bash
docker logs -f torrent-watchdog
```

В логах маскируются секретные параметры URL, например `passkey=***`. Cookie и пароли не выводятся.

## Данные

База SQLite:

```text
./data/app.db
```

Сохранённые `.torrent` файлы:

```text
./data/torrents/<tracked_id>/<info_hash>.torrent
```

В контейнере эти пути находятся в `/data`.
