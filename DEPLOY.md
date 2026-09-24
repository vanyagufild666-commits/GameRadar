# Деплой PartyRadar

PartyRadar работает в Telegram long polling: входящий HTTP-порт и reverse proxy не нужны. Хосту нужен исходящий HTTPS-доступ к `api.telegram.org`, а для SQLite нужен постоянный каталог данных.

## Что требуется

- VPS или Docker-хостинг на Linux/amd64 или arm64.
- Python 3.11+ для native/systemd-варианта или Docker Engine 20.10+.
- Токен бота от `@BotFather`.
- `ADMIN_IDS` можно оставить пустым, если модерация из бота пока не нужна.

`requirements.txt` содержит обе runtime-зависимости бота:

- `python-telegram-bot[job-queue]` нужен для Telegram API и фоновой задачи обслуживания;
- `python-dotenv` нужен для загрузки env-файла.

`pytest` в этом файле нужен для тестов и в runtime бота не импортируется. Образ устанавливает весь `requirements.txt`, чтобы сборка оставалась воспроизводимой одним стандартным файлом; при необходимости уменьшить production-образ вынесите `pytest` в отдельный `requirements-dev.txt` в отдельном изменении.

## Подготовка env-файла

Не добавляйте рабочий env-файл в Git. Создайте его на хосте:

```bash
sudo install -d -m 0750 /etc/partyradar
sudo cp .env.example /etc/partyradar/partyradar.env
sudo chmod 0600 /etc/partyradar/partyradar.env
sudo editor /etc/partyradar/partyradar.env
```

Минимально необходимое значение:

```dotenv
BOT_TOKEN=токен_от_BotFather
```

Остальные параметры:

```dotenv
DB_PATH=bot.db
ADMIN_IDS=123456789,987654321
BOT_USERNAME=PartyRadarBot
CHANNEL_LINK=
CONTACT_LINK=
```

Для systemd `DB_PATH` задаётся самим юнитом как `/var/lib/partyradar/bot.db`. Для Docker используйте `DB_PATH=/data/bot.db`, как в примере ниже. Не копируйте `.env` в образ и не передавайте токен в командной строке, где он попадёт в history/process list.

## Вариант 1: VPS + systemd

Команды ниже рассчитаны на Debian/Ubuntu и выполняются от root или через `sudo`.

### 1. Установить системные пакеты и пользователя

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip rsync
sudo useradd --system --home-dir /opt/partyradar --shell /usr/sbin/nologin partyradar || true
sudo install -d -o partyradar -g partyradar -m 0750 /opt/partyradar /var/lib/partyradar
sudo install -d -o root -g partyradar -m 0750 /etc/partyradar
```

Python 3.11 или новее нужен из-за версии окружения проекта. Если дистрибутив предоставляет более старый Python, установите 3.11+ штатным способом этого дистрибутива и используйте его в командах ниже.

### 2. Скопировать приложение и создать venv

Из каталога проекта на локальной машине:

```bash
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.venv/' \
  --exclude '*.db*' \
  ./ user@VPS:/tmp/partyradar-release/
```

На VPS:

```bash
sudo rsync -a --delete /tmp/partyradar-release/ /opt/partyradar/
sudo chown -R partyradar:partyradar /opt/partyradar
sudo -u partyradar python3 -m venv /opt/partyradar/.venv
sudo -u partyradar /opt/partyradar/.venv/bin/python -m pip install --upgrade pip
sudo -u partyradar /opt/partyradar/.venv/bin/pip install -r /opt/partyradar/requirements.txt
```

Установите env-файл из раздела выше. Убедитесь, что `/var/lib/partyradar` принадлежит `partyradar` и имеет режим `0750`.

### 3. Установить и запустить юнит

В репозитории юнит лежит в `systemd/partyradar.service`. Перед установкой замените `ExecStart` на venv-интерпретатор:

```bash
sudo sed -i 's#ExecStart=/usr/bin/python3#ExecStart=/opt/partyradar/.venv/bin/python#' \
  /opt/partyradar/systemd/partyradar.service
sudo install -m 0644 /opt/partyradar/systemd/partyradar.service \
  /etc/systemd/system/partyradar.service
sudo systemctl daemon-reload
sudo systemctl enable --now partyradar.service
```

Проверка запуска и логов:

```bash
sudo systemctl status --no-pager partyradar.service
sudo journalctl -u partyradar.service -n 100 --no-pager
sudo journalctl -u partyradar.service -f
```

Ожидаемый признак успешного запуска в журнале: `PartyRadar запускается`. Если токен не задан, процесс завершится с кодом `2` и сообщением `BOT_TOKEN не задан`.

### Обновление

```bash
rsync -a --delete \
  --exclude '.git/' --exclude '.env' --exclude '.venv/' --exclude '*.db*' \
  ./ user@VPS:/tmp/partyradar-release/
sudo systemctl stop partyradar.service
sudo rsync -a --delete /tmp/partyradar-release/ /opt/partyradar/
sudo chown -R partyradar:partyradar /opt/partyradar
sudo -u partyradar /opt/partyradar/.venv/bin/pip install -r /opt/partyradar/requirements.txt
sudo systemctl start partyradar.service
sudo systemctl status --no-pager partyradar.service
```

База находится отдельно в `/var/lib/partyradar`, поэтому обновление приложения её не удаляет.

## Вариант 2: Docker-хостинг

### Сборка

В каталоге проекта:

```bash
docker build -t partyradar:latest .
```

`.dockerignore` исключает `.env`, SQLite-файлы, Git-метаданные, кэш тестов и deployment-документацию. В образе запускается непривилегированный пользователь `partyradar`.

### Запуск

Создайте env-файл вне репозитория, например `/opt/partyradar/partyradar.env`, с `BOT_TOKEN` и нужными настройками. Затем создайте постоянный volume и запустите контейнер:

```bash
docker volume create partyradar-data
docker run -d \
  --name partyradar \
  --restart unless-stopped \
  --env-file /opt/partyradar/partyradar.env \
  -e DB_PATH=/data/bot.db \
  -v partyradar-data:/data \
  partyradar:latest
```

Порты публиковать не нужно. Проверка:

```bash
docker ps --filter name=partyradar
docker logs --tail=100 partyradar
docker exec partyradar python -c 'import config; print("config ok")'
```

При использовании панели Docker-хостинга задайте образ `partyradar:latest`, env-переменные из `.env.example`, persistent volume, смонтированный в `/data`, и policy перезапуска `unless-stopped`. Команду запуска менять не нужно: она задана в Dockerfile.

### Обновление образа

```bash
docker build -t partyradar:latest .
docker stop partyradar
docker rm partyradar
docker run -d \
  --name partyradar \
  --restart unless-stopped \
  --env-file /opt/partyradar/partyradar.env \
  -e DB_PATH=/data/bot.db \
  -v partyradar-data:/data \
  partyradar:latest
docker logs --tail=100 partyradar
```

Не выполняйте `docker volume rm partyradar-data`: это удалит SQLite-базу. Перед обновлением сделайте резервную копию:

```bash
docker run --rm \
  -v partyradar-data:/data:ro \
  -v "$PWD":/backup \
  alpine:3.20 sh -c 'tar czf /backup/partyradar-data-$(date +%Y%m%d-%H%M%S).tgz -C /data .'
```

## Резервное копирование SQLite

Для systemd остановите бота перед копированием, чтобы получить согласованный архив:

```bash
sudo systemctl stop partyradar.service
sudo tar -C /var/lib/partyradar -czf \
  /var/backups/partyradar-$(date +%Y%m%d-%H%M%S).tgz .
sudo systemctl start partyradar.service
```

Храните резервные копии вне VPS и периодически проверяйте восстановление. Не публикуйте архивы базы: они содержат пользовательские анкеты и Telegram file ID.

## Проверка перед релизом

Локально:

```bash
python -m pytest -q
python -m compileall -q .
docker build -t partyradar:check .
```

Проверка секрета в Git:

```bash
git ls-files -z | xargs -0 grep -nE '(BOT_TOKEN|TELEGRAM_TOKEN|API_KEY|SECRET|PASSWORD)[[:space:]]*=[[:space:]]*[^[:space:]#]'
```

Эта команда не должна выводить реальные значения. `.env` должен оставаться untracked, а в `.env.example` должны быть только пустые или демонстрационные значения.

После запуска проверьте:

1. контейнер или systemd-юнит остаётся в состоянии running/active;
2. в логах нет ошибки авторизации Telegram;
3. отправка `/start` боту отвечает;
4. после регистрации создаётся `/data/bot.db` или `/var/lib/partyradar/bot.db`;
5. после перезапуска данные пользователя сохраняются.

## Безопасность и эксплуатация

- Не коммитьте `.env`, токен, архивы SQLite и логи.
- Не публикуйте входящие порты для polling-бота.
- Ограничьте доступ к env-файлу режимом `0600`.
- Не запускайте контейнер от root и не выдавайте ему лишние capabilities.
- Следите за размером базы и свободным местом на диске.
- Ротацию и хранение journald/Docker-логов настройте по политике вашего хостинга.
- Для смены токена обновите env-файл и перезапустите сервис или контейнер; новый токен не нужно встраивать в образ.
