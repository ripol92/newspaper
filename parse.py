from newspaper import Article
import redis
import json

r = redis.Redis(host='localhost', port=6379, db=0)
p = r.pubsub()
p.subscribe('urls_for_parse')

while True:
	message = p.get_message()
	if message and not message['data'] == 1:
		print(message)
		message = json.loads(message['data'])
		url = message["url"]
		lng = message["lng"]
		article = Article(url, _language=lng)
		article.download()
		article.parse()
		text = article.text
		imageLinks = article.images
		if isinstance(imageLinks, set):
		    imageLinks = list(imageLinks)
		r.publish('parsed_urls', json.dumps({"article": text, "image_links": imageLinks, "url": url}))
