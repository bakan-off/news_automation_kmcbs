import logging
from flask import Flask, request, render_template, redirect, url_for, session, flash
from webdav3.client import Client
import os
from datetime import datetime
import tempfile
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Настройка логирования
logging.basicConfig(
    filename='error.log',  # Имя файла для логов ошибок
    level=logging.ERROR,  # Уровень логирования
    format='%(asctime)s:%(levelname)s:%(message)s'
)

# Создание экземпляра приложения Flask
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your_default_secret_key')

# Данные для подключения к WebDAV
data = {
    'webdav_hostname': "https://webdav.cloud.mail.ru",
    'webdav_login': os.getenv('WEBDAV_LOGIN'),
    'webdav_password': os.getenv('WEBDAV_PASSWORD')
}
client = Client(data)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['POST'])
def login():
    user_cod = request.form['user_cod']
    password_cod = request.form['password_cod']

    if user_cod == os.getenv('USER_COD') and password_cod == os.getenv('PASSWORD_COD'):
        session['logged_in'] = True
        return redirect(url_for('index'))
    else:
        flash('Неверный логин или пароль')
        return redirect(url_for('index'))


@app.route('/submit_news', methods=['POST'])
def submit_news():
    if 'logged_in' not in session:
        flash('Вы должны войти в систему, чтобы отправить новость.')
        return redirect(url_for('index'))

    title = request.form['title'].strip()
    age_rating = request.form['age_rating'].strip()
    description = request.form['description'].strip().replace('  ', ' ')  # Удаление двойных пробелов
    author = request.form['author'].strip()
    hashtags = request.form['hashtags'].strip()

    # Создаем уникальное имя папки с только датой и временем
    date = datetime.now().strftime("%d%m%Y-%H%M%S")
    folder_name = date  # Имя папки теперь только дата и время

    if not client.check(folder_name):
        client.mkdir(folder_name)

    file_urls = []
    files = request.files.getlist('files')
    for file in files:
        if file and file.content_length <= 1 * 1024 * 1024 * 1024:  # Проверка на размер <= 1 Гб
            # Обработка имени файла: замена пробелов на подчеркивания
            safe_filename = file.filename.replace(" ", "_")
            temp_file_path = os.path.join(tempfile.gettempdir(), safe_filename)
            file.save(temp_file_path)  # Сохраняем файл во временный файл
            try:
                client.upload_sync(remote_path=f"{folder_name}/{safe_filename}", local_path=temp_file_path)
                file_urls.append(f"https://webdav.cloud.mail.ru/{folder_name}/{safe_filename}")
            except Exception as e:
                logging.error(f"Ошибка при загрузке файла {safe_filename}: {e}")
            finally:
                # Удаляем временный файл после загрузки
                try:
                    os.remove(temp_file_path)
                except Exception as e:
                    logging.error(f"Ошибка при удалении временного файла {temp_file_path}: {e}")

    # Отправка письма с новостью
    try:
        send_email(title, description, author, age_rating, hashtags, file_urls, folder_name)
    except Exception as e:
        logging.error(f"Ошибка при отправке письма: {e}")

    return redirect(url_for('index'))


def send_email(title, description, author, age_rating, hashtags, file_urls, folder_name):
    # Настройки почты
    sender_email = os.getenv('SENDER_EMAIL')
    sender_password = os.getenv('SENDER_PASSWORD')
    receiver_email = 'muk_kmcbs_smi@mail.ru'  # Замените на адрес получателя

    # Создаем сообщение
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = f"Новая новость: {title} ({age_rating})"

    # Формируем тело письма
    folder_link = f"https://cloud.mail.ru/home/{folder_name}/"  # Обновленное имя папки
    body = f"""{title}
{description}
{author}
{hashtags}

Ссылка на папку {title} ({age_rating}): {folder_link}

Ссылки на загруженные файлы:
"""
    for url in file_urls:
        # Замена в ссылках на файлы
        file_link = url.replace("https://webdav.cloud.mail.ru/", "https://cloud.mail.ru/home/")
        body += f"{file_link}\n"

    msg.attach(MIMEText(body, 'plain'))

    # Отправка письма через SMTP
    try:
        with smtplib.SMTP_SSL('smtp.mail.ru', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
            print("Письмо успешно отправлено")
    except Exception as e:
        logging.error(f"Ошибка при отправке письма: {e}")


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)