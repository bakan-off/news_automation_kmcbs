import html
import re
import os
import tempfile
import smtplib
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from flask import Flask, request, render_template, redirect, url_for, session, flash
from webdav3.client import Client
from dotenv import load_dotenv

# Загрузка переменных окружения из .env при локальной разработке
load_dotenv()

# Настройка логирования с ротацией и выводом в консоль (для логов Timeweb)
formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(message)s')

file_handler = RotatingFileHandler('error.log', maxBytes=1000000, backupCount=5, encoding='utf-8')
file_handler.setLevel(logging.ERROR)
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Создание экземпляра приложения Flask
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your_default_secret_key')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 МБ
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Данные для подключения к WebDAV
data = {
    'webdav_hostname': "https://webdav.cloud.mail.ru",
    'webdav_login': os.getenv('WEBDAV_LOGIN'),
    'webdav_password': os.getenv('WEBDAV_PASSWORD')
}
client = Client(data)

def sanitize_filename(filename):
    """Безопасная очистка имени файла с сохранением русских букв."""
    if not filename:
        return "file"
    filename = os.path.basename(filename)
    filename = filename.replace(" ", "_")
    # Оставляем буквы (включая кириллицу), цифры, точки, тире, подчеркивания
    filename = re.sub(r'[^\w\.\-\u0400-\u04FF]', '_', filename)
    filename = re.sub(r'_{2,}', '_', filename)
    filename = re.sub(r'\.{2,}', '.', filename)
    return filename.strip('._') or "file"

@app.errorhandler(413)
def request_entity_too_large(error):
    flash('Общий размер загружаемых файлов превышает 500 Мб.')
    return redirect(url_for('index'))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/remote_request')
def remote_request():
    return render_template('remote_request.html')

@app.route('/login', methods=['POST'])
def login():
    user_cod = request.form.get('user_cod', '').strip()
    password_cod = request.form.get('password_cod', '').strip()
    author = request.form.get('author', '').strip()

    if user_cod == os.getenv('USER_COD') and password_cod == os.getenv('PASSWORD_COD'):
        session['logged_in'] = True
        session['author'] = author
        return redirect(url_for('index'))
    else:
        flash('Неверный логин или пароль')
        return redirect(url_for('index'))

@app.route('/submit_news', methods=['POST'])
def submit_news():
    if 'logged_in' not in session:
        flash('Вы должны войти в систему, чтобы отправить новость.')
        return redirect(url_for('index'))

    title = request.form.get('title', '').strip()
    age_rating = request.form.get('age_rating', '0+').strip()
    raw_desc = request.form.get('description', '')
    # Очищаем только лишние горизонтальные пробелы в строках, сохраняя все переносы строк и абзацы
    description = re.sub(r'[^\S\r\n]{2,}', ' ', raw_desc).strip()
    author = session.get('author', '').strip()
    raw_hashtags = request.form.get('hashtags', '').strip()
    # Автоматически разделяем слитные хештеги пробелами (#тег1#тег2 -> #тег1 #тег2)
    hashtags = re.sub(r'\s+', ' ', re.sub(r'(?<!^)(?<!\s)#', ' #', raw_hashtags)).strip()

    social_media = request.form.getlist('social_media')
    logging.info(f"Выбранные социальные сети: {social_media}")

    # Создаем имя папки на основе текущей даты и времени
    folder_name = datetime.now().strftime("%d%m%Y-%H%M%S")

    files = [f for f in request.files.getlist('files') if f and f.filename]
    file_urls = []
    upload_errors = []

    if files:
        try:
            if not client.check(folder_name):
                client.mkdir(folder_name)
        except Exception as e:
            logging.error(f"Ошибка при создании папки {folder_name} в WebDAV: {e}")
            upload_errors.append(f"Ошибка создания папки на сервере хранения: {e}")

        # Изолированная временная директория для безопасной параллельной загрузки
        with tempfile.TemporaryDirectory() as temp_dir:
            used_filenames = set()
            for file in files:
                base_safe_name = sanitize_filename(file.filename)
                safe_filename = base_safe_name
                counter = 1
                while safe_filename in used_filenames:
                    name_part, ext_part = os.path.splitext(base_safe_name)
                    safe_filename = f"{name_part}_{counter}{ext_part}"
                    counter += 1
                used_filenames.add(safe_filename)

                temp_file_path = os.path.join(temp_dir, safe_filename)
                file.save(temp_file_path)
                try:
                    client.upload_sync(remote_path=f"{folder_name}/{safe_filename}", local_path=temp_file_path)
                    file_urls.append(f"https://webdav.cloud.mail.ru/{folder_name}/{safe_filename}")
                    logging.info(f"Файл {safe_filename} успешно загружен в {folder_name}.")
                except Exception as e:
                    logging.error(f"Ошибка при загрузке файла {safe_filename}: {e}")
                    upload_errors.append(f"Ошибка при загрузке файла {safe_filename}: {e}")

    # Отправка письма с новостью
    try:
        send_email(title, description, author, age_rating, hashtags, file_urls, folder_name, social_media)
    except Exception as e:
        logging.error(f"Ошибка при отправке письма: {e}")
        flash(f"Ошибка при отправке письма: {e}. Пожалуйста, попробуйте позже.")
        return redirect(url_for('index'))

    if upload_errors:
        for err in upload_errors:
            flash(err)
        flash(f"{author}, новость отправлена, но часть файлов не удалось загрузить.")
    else:
        flash(f"{author}, новость отправлена. Спасибо за вашу работу!")

    return redirect(url_for('index'))

def send_email(title, description, author, age_rating, hashtags, file_urls, folder_name, social_media):
    sender_email = os.getenv('SENDER_EMAIL')
    sender_password = os.getenv('SENDER_PASSWORD')
    receiver_email = os.getenv('RECEIVER_EMAIL')

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = f"Новая новость: {title} ({age_rating})"

    folder_link = f"https://cloud.mail.ru/home/{folder_name}/"

    buttons_html = ""
    links = {
        "МЦБС-Конда.рф": "http://xn----8sbbn3ajkhy2c.xn--p1ai/admin/index.html",
        "Вконтакте": "https://vk.com/mukmcbs",
        "Одноклассники": "https://ok.ru/mukmcbs",
        "Telegram": "https://t.me/mukkmcbs"
    }
    for media in social_media:
        if media in links:
            safe_media = html.escape(media)
            buttons_html += f'<a href="{links[media]}" style="display: inline-block; margin: 5px; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px;">{safe_media}</a>'

    safe_title = html.escape(title)
    safe_author = html.escape(author)
    safe_age_rating = html.escape(age_rating)
    normalized_hashtags = re.sub(r'\s+', ' ', re.sub(r'(?<!^)(?<!\s)#', ' #', hashtags)).strip()
    safe_hashtags = html.escape(normalized_hashtags)

    # Разбиваем текст описания на отдельные абзацы и оборачиваем каждый в чистый тег <p>
    # Это обеспечивает автоматические пустые строки-отступы после названия, между абзацами, перед подписью автора и хештегами при копировании
    paragraphs = [p.strip() for p in re.split(r'\r?\n+', description) if p.strip()]
    if paragraphs:
        description_html = "\n        ".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    else:
        description_html = f"<p>{html.escape(description)}</p>"

    html_body = f"""
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body>
        <p><strong>{safe_title} ({safe_age_rating})</strong></p>
        {description_html}
        <p>{safe_author}</p>
        <p>{safe_hashtags}</p>
        <div style="border: 1px solid #ddd; padding: 10px; margin-top: 20px;">
            <p>Ссылка на папку {safe_title} ({safe_age_rating}): <a href="{folder_link}">{folder_link}</a></p>
            <p>Ссылки на загруженные файлы:</p>
            <ul>
    """
    for url in file_urls:
        file_link = url.replace("https://webdav.cloud.mail.ru/", "https://cloud.mail.ru/home/")
        html_body += f"<li><a href='{file_link}'>{file_link}</a></li>"
    html_body += "</ul>"

    if social_media:
        html_body += "<p>Где публиковать новость:</p><ul>"
        for media in social_media:
            html_body += f"<li>{html.escape(media)}</li>"
        html_body += "</ul>"

    html_body += f"<p>{buttons_html}</p></div></body></html>"

    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    with smtplib.SMTP_SSL('smtp.mail.ru', 465) as server:
        server.login(sender_email, sender_password)
        server.send_message(msg)
        print("Письмо успешно отправлено")

@app.route('/submit_remote_request', methods=['POST'])
def submit_remote_request():
    full_name = request.form.get('fullName', '').strip()
    phone = request.form.get('phone', '').strip()
    problem_option = request.form.get('problemOption', 'phone')
    problem_description = request.form.get('problemDescription', '').strip()
    remote_software = request.form.get('remoteSoftware', '')
    access_option = request.form.get('accessOption', 'provideData')
    access_data = request.form.get('accessData', '').strip()
    screenshot = request.files.get('screenshot')

    # Формирование тела письма
    problem_text = 'О проблеме расскажу по телефону при удаленном подключении' if problem_option == 'phone' else f'Описание проблемы: {problem_description}'
    access_text = f'Данные для доступа: {access_data}' if access_option == 'provideData' else 'Скриншот для доступа прикреплен.'

    subject = f"Запрос на удалённое подключение. {full_name} {phone}"
    body = f"""
    Имя: {full_name}
    Телефон: {phone}

    {problem_text}

    Подключение через: {remote_software}

    Доступ:
    {access_text}
    """

    sender_email = os.getenv('SENDER_EMAIL')
    sender_password = os.getenv('SENDER_PASSWORD')
    receiver_email = os.getenv('RECEIVER_EMAIL')

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # Прикрепление скриншота при наличии
    if screenshot and screenshot.filename:
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(screenshot.read())
        encoders.encode_base64(part)
        safe_screenshot_name = sanitize_filename(screenshot.filename)
        part.add_header('Content-Disposition', f'attachment; filename="{safe_screenshot_name}"')
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL('smtp.mail.ru', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
            print("Письмо успешно отправлено")
    except Exception as e:
        logging.error(f"Ошибка при отправке письма: {e}")
        flash('Ошибка при отправке запроса. Пожалуйста, попробуйте позже.')
        return redirect(url_for('remote_request'))

    flash(f'{full_name}, подключение запланировано. Спасибо!')
    return redirect(url_for('remote_request'))

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('author', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
