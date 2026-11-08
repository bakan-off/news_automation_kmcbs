import os
import unittest
from unittest.mock import patch, MagicMock
import io
import html
import re

# Устанавливаем тестовые переменные окружения
os.environ['USER_COD'] = 'test_user'
os.environ['PASSWORD_COD'] = 'test_pass'
os.environ['SECRET_KEY'] = 'test_secret_key_12345'
os.environ['SENDER_EMAIL'] = 'sender@example.com'
os.environ['SENDER_PASSWORD'] = 'sender_pass'
os.environ['RECEIVER_EMAIL'] = 'receiver@example.com'

from app import app, sanitize_filename

class NewsAutomationTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.test_client()

    def test_01_static_bootstrap_assets(self):
        """Проверка доступности и полноты локального дистрибутива Bootstrap."""
        res_css = self.client.get('/static/vendor/bootstrap/bootstrap.min.css')
        self.assertEqual(res_css.status_code, 200)
        self.assertGreater(len(res_css.data), 200000, "Bootstrap CSS должен быть полным официальным файлом (>200KB)")
        res_css.close()

        res_js = self.client.get('/static/vendor/bootstrap/bootstrap.bundle.min.js')
        self.assertEqual(res_js.status_code, 200)
        self.assertGreater(len(res_js.data), 70000, "Bootstrap JS должен быть полным официальным файлом (>70KB)")
        res_js.close()

    def test_02_index_and_remote_pages(self):
        """Проверка загрузки страниц, отсутствия деструктивного JS и корректности темы."""
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        html_text = res.data.decode('utf-8')
        
        # Проверяем, что нет деструктивного replace(/\s{2,}/g, ' ')
        self.assertNotIn(r"\s{2,}", html_text, "Деструктивное регулярное выражение не должно присутствовать в коде")
        
        # Проверяем наличие ранней инициализации темы в head
        self.assertIn("localStorage.getItem('theme')", html_text)
        
        # Проверяем страницу remote_request
        res_remote = self.client.get('/remote_request')
        self.assertEqual(res_remote.status_code, 200)

    def test_03_login_and_logout(self):
        """Проверка авторизации с верными и неверными данными."""
        # Неверные данные
        res = self.client.post('/login', data={
            'user_cod': 'wrong',
            'password_cod': 'wrong',
            'author': 'Тестовый автор'
        }, follow_redirects=True)
        self.assertIn('Неверный логин или пароль'.encode('utf-8'), res.data)

        # Верные данные
        with self.client:
            res = self.client.post('/login', data={
                'user_cod': 'test_user',
                'password_cod': 'test_pass',
                'author': 'Библиотекарь Анна'
            }, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            from flask import session
            self.assertTrue(session.get('logged_in'))
            self.assertEqual(session.get('author'), 'Библиотекарь Анна')

            # Выход
            self.client.get('/logout')
            self.assertFalse(session.get('logged_in'))

    def test_04_sanitize_filename(self):
        """Проверка безопасной очистки имен файлов с поддержкой русского языка."""
        self.assertEqual(sanitize_filename("Новость_от_07_ноября.docx"), "Новость_от_07_ноября.docx")
        self.assertEqual(sanitize_filename("Праздник осени 2026.jpg"), "Праздник_осени_2026.jpg")
        self.assertEqual(sanitize_filename("../../../etc/shadow"), "shadow")
        self.assertEqual(sanitize_filename("..\\..\\windows\\cmd.exe"), "cmd.exe")
        self.assertEqual(sanitize_filename("файл???***<<<>>>.pdf"), "файл_.pdf")

    @patch('app.smtplib.SMTP_SSL')
    @patch('app.client')
    def test_05_multiline_description_preserved_in_email(self, mock_webdav, mock_smtp):
        """Главный тест: проверка сохранения структуры абзацев, переносов строк и защиты от XSS в email."""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        mock_webdav.check.return_value = False

        # Текст новости с несколькими абзацами, пустой строкой, списками и HTML-символами
        test_multiline_text = (
            "Уважаемые читатели!\n\n"
            "Приглашаем вас на выставку «Книги & Люди <2026>».\n\n"
            "В программе:\n"
            "- Встреча с писателем;\n"
            "- Викторина для школьников;\n"
            "- Чайная пауза.\n\n"
            "Ждем всех!"
        )

        with self.client:
            # Логинимся
            self.client.post('/login', data={
                'user_cod': 'test_user',
                'password_cod': 'test_pass',
                'author': 'ЦБ им. Шесталова'
            })

            # Отправляем новость с файлом и слитными хештегами
            data = {
                'title': 'Выставка «Книги & Люди»',
                'age_rating': '6+',
                'description': test_multiline_text,
                'hashtags': '#Россияобъединяет#Культурадляшкольников#ДетиРулят86',
                'social_media': ['Вконтакте', 'Telegram'],
                'files': (io.BytesIO(b"dummy image data"), "Фото_мероприятия.jpg")
            }

            res = self.client.post('/submit_news', data=data, content_type='multipart/form-data', follow_redirects=True)
            self.assertEqual(res.status_code, 200)

            # Проверяем, что письмо было отправлено
            self.assertTrue(mock_server.send_message.called)
            sent_msg = mock_server.send_message.call_args[0][0]

            # Получаем HTML-тело отправленного письма
            email_payload = sent_msg.get_payload()
            html_part = None
            for part in email_payload:
                if part.get_content_type() == 'text/html':
                    html_part = part.get_payload(decode=True).decode('utf-8')
                    break

            self.assertIsNotNone(html_part, "В письме должна присутствовать HTML-часть")

            # Проверяем 1: XSS экранирован
            self.assertNotIn("<2026>", html_part, "Символы < и > должны быть экранированы как &lt; и &gt;")
            self.assertIn("&lt;2026&gt;", html_part)

            # Проверяем 2: каждый абзац обернут в отдельный чистый тег <p>...</p>
            self.assertIn("<p><strong>Выставка «Книги &amp; Люди» (6+)</strong></p>", html_part)
            self.assertIn("<p>Уважаемые читатели!</p>", html_part)
            self.assertIn("<p>Приглашаем вас на выставку «Книги &amp; Люди &lt;2026&gt;».</p>", html_part)
            self.assertIn("<p>- Встреча с писателем;</p>", html_part)
            self.assertIn("<p>ЦБ им. Шесталова</p>", html_part)

            # Проверяем 3: нет лишних надписей "Автор / библиотека:" и "Хештеги:", мешающих копированию
            self.assertNotIn("Автор / библиотека:", html_part)
            self.assertNotIn("Хештеги:", html_part)

            # Проверяем 4: слитные хештеги автоматически разделены пробелами
            self.assertIn("<p>#Россияобъединяет #Культурадляшкольников #ДетиРулят86</p>", html_part)

            # Проверяем 5: загрузка файла в WebDAV была вызвана с безопасным именем
            self.assertTrue(mock_webdav.upload_sync.called)
            remote_path_arg = mock_webdav.upload_sync.call_args[1]['remote_path']
            self.assertTrue(remote_path_arg.endswith("Фото_мероприятия.jpg"))

    @patch('app.smtplib.SMTP_SSL')
    def test_06_remote_request(self, mock_smtp):
        """Проверка отправки заявки на удаленное подключение."""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        data = {
            'fullName': 'Иванов Иван',
            'phone': '+79001234567',
            'problemOption': 'description',
            'problemDescription': 'Не открывается программа каталогизации',
            'remoteSoftware': 'AweRay',
            'accessOption': 'provideData',
            'accessData': 'ID: 123 456 789, Pass: 9876',
            'screenshot': (io.BytesIO(b"fake image bytes"), "screen.png")
        }

        res = self.client.post('/submit_remote_request', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Иванов Иван, подключение запланировано'.encode('utf-8'), res.data)
        self.assertTrue(mock_server.send_message.called)

    @patch('app.smtplib.SMTP_SSL')
    def test_07_smtp_failure_handling(self, mock_smtp):
        """Проверка: при сбое почтового сервера пользователь получает сообщение об ошибке, а не поздравление."""
        mock_smtp.side_effect = Exception("SMTP connection refused")

        with self.client:
            self.client.post('/login', data={
                'user_cod': 'test_user',
                'password_cod': 'test_pass',
                'author': 'Тестовый библиотекарь'
            })

            data = {
                'title': 'Тест ошибки SMTP',
                'age_rating': '0+',
                'description': 'Тестовое описание',
                'hashtags': '#Тест',
                'social_media': []
            }

            res = self.client.post('/submit_news', data=data, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn('Ошибка при отправке письма'.encode('utf-8'), res.data)
            self.assertNotIn('новость успешно отправлена'.encode('utf-8'), res.data)

if __name__ == '__main__':
    unittest.main()
