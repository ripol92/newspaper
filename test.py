from newspaper import Article
import mysql.connector

mydb = mysql.connector.connect(
  host="127.0.0.1",
  user="root",
  password="",
  database="python",
  port="3306"
)

url = 'https://github.com/'
article = Article(url, _language='tg')
article.download()
article.parse()

mycursor = mydb.cursor()
sql = "INSERT INTO test (text, url) VALUES (%s, %s)"
val = (article.text, url)
mycursor.execute(sql, val)

mydb.commit()

print(url)