# -*- coding: utf-8 -*-

"""
Article objects abstract an online news article page.
"""
import logging
import copy
import os
import glob

from . import nlp
from . import images
from . import utils
from . import network
from .configuration import Configuration
from .extractors import StandardContentExtractor
from .text_utils import URLHelper, RawHelper
from .cleaners import StandardDocumentCleaner
from .outputformatters import StandardOutputFormatter
from .videos.extractors import VideoExtractor
from .urls import (
    prepare_url, get_domain, get_scheme, valid_url)

log = logging.getLogger(__name__)

class ArticleException(Exception):
    pass

class Article(object):

    def __init__(self, url, title=u'', source_url=None, configs=None):

        self.configs = Configuration() if not configs else configs
        self.parser = self.configs.get_parser()
        self.extractor = StandardContentExtractor(config=self.configs)

        if source_url is None:
            source_url = get_scheme(url) + '://' + get_domain(url)

        if source_url is None or source_url == '':
            raise ArticleException('input url bad format')

        # if no attached source object, we just fallback on scheme + domain of url
        self.source_url = utils.fix_unicode(source_url)

        self.url = utils.fix_unicode(url)
        self.url = prepare_url(self.url, self.source_url)

        self.title = utils.fix_unicode(title)

        # the url of the "best image" to represent this article
        self.top_img = u''

        # all image urls
        self.imgs = []

        # pure text from the article
        self.text = u''

        # keywords extracted from the cleaned text
        self.keywords = []

        # list of authors who have published the article
        self.authors = []

        self.published_date = u'' # TODO

        # summary generated from the article's body txt
        self.summary = u''

        self.html = u''

        # flags warning users in-case they forget to download() or parse()
        self.is_parsed = False
        self.is_downloaded = False

        # meta description field in HTML source
        self.meta_description = u""

        # meta lang field in HTML source
        self.meta_lang = u""

        # meta favicon field in HTML source
        self.meta_favicon = u""

        # meta keywords field in the HTML source
        self.meta_keywords = u""

        # The canonical link of this article if found in the meta data
        self.canonical_link = u""

        # holds the top Element we think
        # is a candidate for the main body of the article
        self.top_node = None

        # holds a set of tags that may have
        # been in the article, these are not meta keywords
        self.tags = set()

        # list of any movies found on the page like: youtube & vimeo
        self.movies = []

        # stores the final URL that we're going to try
        # and fetch content against, this would be expanded if any
        self.final_url = u""

        # stores the MD5 hash of the url
        # to use for various identification tasks
        self.link_hash = u""

        # the lxml doc object
        self.doc = None

        # a pure object from the orig html without any cleaning options done on it
        self.raw_doc = None

        # A property bucket for consumers of goose to store custom data extractions.
        self.additional_data = {}

    def build(self):
        """
        build a lone article from a url independent of the
        source (newspaper). We won't normally call this method b/c
        we want to multithread articles on a source (newspaper) level
        """
        self.download()
        self.parse()
        self.nlp()

    def get_key(self):
        """
        returns a md5 representation of the url
        """
        if not self.is_parsed:
            raise ArticleException('You must parse an article '
                                   'before asking for a link_hash')
        return self.link_hash

    def download(self, timeout=7):
        """
        downloads the link's html content, don't use if we are async
        downloading batch articles
        """
        self.html = network.get_html(self.url, timeout=timeout)
        self.is_downloaded = True

    def parse(self):
        """
        extracts the lxml root (doc), if lxml fails, we also extract the
        BeautifulSoup root. We also parse images to keep cpu bound
        tasks all in one place
        """
        if not self.is_downloaded:
            print 'You must download an article before parsing it! run download()'
            raise ArticleException()

        self.doc = self.parser.fromstring(self.html)
        self.raw_doc = copy.deepcopy(self.doc)

        parse_candidate = self.get_parse_candidate(self)
        document_cleaner = self.get_document_cleaner()
        output_formatter = self.get_output_formatter()

        self.final_url = parse_candidate.url
        self.link_hash = parse_candidate.link_hash
        title = self.extractor.get_title(self)
        authors = self.extractor.get_authors(self)

        # TODO self.publish_date = self.config.publishDateExtractor.extract(self.doc)
        # TODO self.additional_data = self.config.get_additionaldata_extractor.extract(self.doc)

        self.meta_lang = self.extractor.get_meta_lang(self)
        self.meta_favicon = self.extractor.get_favicon(self)
        self.meta_description = self.extractor.get_meta_description(self)
        self.canonical_link = self.extractor.get_canonical_link(self)
        self.tags = self.extractor.extract_tags(self)

        meta_keywords = self.extractor.get_meta_keywords(self)
        self.meta_keywords = [k.strip() for k in meta_keywords.split(',')]

        # before we do any computations on the body itself, we must clean up the document
        self.doc = document_cleaner.clean(self)

        text = u''
        self.top_node = self.extractor.calculate_best_node(self)
        if self.top_node is not None:
            video_extractor = self.get_video_extractor(self)
            video_extractor.get_videos()

            self.top_node = self.extractor.post_cleanup(self.top_node)
            text = output_formatter.get_formatted_text(self)

        self.set_title(title)
        self.set_authors(authors)
        self.set_text(text)
        self.set_keywords(self.meta_keywords)

        # cleanup tmp file
        self.relase_resources(self)

        if self.raw_doc is not None:
            img_url = self.extractor.get_top_img_url(self)
            self.top_img = utils.fix_unicode(img_url)

            top_imgs = self.extractor.get_img_urls(self)
            top_imgs = [ utils.fix_unicode(t) for t in top_imgs ]
            self.imgs = top_imgs

        self.set_reddit_top_img()
        self.is_parsed = True

    def is_valid_url(self):
        """
        performs a check on the url of this link to
        determine if a real news article or not
        """
        return valid_url(self.url)

    def is_valid_body(self):
        """
        if the article's body text is long enough to meet
        standard article requirements, we keep the article
        """
        if not self.is_parsed:
            raise ArticleException('must parse article before checking \
                                    if it\'s body is valid!')
        meta_type = self.parser.get_meta_type(self.raw_doc)
        wordcount = self.text.split(' ')
        sentcount = self.text.split('.')

        if meta_type == 'article' and wordcount > (self.configs.MIN_WORD_COUNT - 50):
            log.debug('%s verified for article and wc' % self.url)
            return True

        if not self.is_media_news() and not self.text:
            log.debug('%s caught for no media no text' % self.url)
            return False

        if self.title is None or len(self.title.split(' ')) < 2:
            log.debug('%s caught for bad title' % self.url)
            return False

        if len(wordcount) < self.configs.MIN_WORD_COUNT:
            log.debug('%s caught for word cnt' % self.url)
            return False

        if len(sentcount) < self.configs.MIN_SENT_COUNT:
            log.debug('%s caught for sent cnt' % self.url)
            return False

        if self.html is None or self.html == u'':
            log.debug('%s caught for no html' % self.url)
            return False

        log.debug('%s verified for default true' % self.url)
        return True

    def is_media_news(self):
        """
        if the article is a gallery, video, etc related
        """
        safe_urls = [
            '/video', '/slide', '/gallery', '/powerpoint', '/fashion',
            '/glamour', '/cloth']
        for s in safe_urls:
            if s in self.url:
                return True
        return False

    def nlp(self):
        """
        keyword extraction wrapper
        """
        if not self.is_downloaded or not self.is_parsed:
            print 'You must download and parse an article before parsing it!'
            raise ArticleException()

        text_keyws = nlp.keywords(self.text).keys()
        title_keyws = nlp.keywords(self.title).keys()
        keyws = list(set(title_keyws + text_keyws))
        self.set_keywords(keyws)

        summary_sents = nlp.summarize(title=self.title, text=self.text)
        summary = '\r\n'.join(summary_sents)
        self.set_summary(summary)

    def get_parse_candidate(self, crawl_candidate):
        """
        A parse candidate is a wrapper object holding a link hash of this
        article and a final_url
        """
        if crawl_candidate.html:
            return RawHelper.get_parsing_candidate(
                crawl_candidate.url, crawl_candidate.html)
        return URLHelper.get_parsing_candidate(crawl_candidate.url)

    def get_video_extractor(self, article):
        return VideoExtractor(article, self.configs)

    def get_output_formatter(self):
        return StandardOutputFormatter(self.configs)

    def get_document_cleaner(self):
        return StandardDocumentCleaner(self.configs)

    def get_extractor(self):
        return StandardContentExtractor(self.configs)

    def relase_resources(self, article):
        """
        """
        path = os.path.join(self.configs.local_storage_path, '%s_*' % article.link_hash)
        for fname in glob.glob(path):
            try:
                os.remove(fname)
            except OSError:
                # TODO better log handling
                pass

    def set_reddit_top_img(self):
        """
        wrapper for setting images, queries known image attributes
        first, uses Reddit's img algorithm as a fallback
        """
        if self.top_img != u'': # if we already have a top img...
            return
        try:
            s = images.Scraper(self)
            self.top_img = s.largest_image_url()
        except Exception, e:
            log.critical('jpeg error with PIL, %s' % e)

    def set_title(self, title):
        """
        titles are length limited
        """
        title = title[:self.configs.MAX_TITLE]
        title = utils.fix_unicode(title)
        if title:
            self.title = title

    def set_text(self, text):
        """
        text is length limited
        """
        text = text[:self.configs.MAX_TEXT-5]
        text = utils.fix_unicode(text)
        if text:
            self.text = text

    def set_keywords(self, keywords):
        """
        keys are stored in list format
        """
        if not isinstance(keywords, list):
            raise Exception("Keyword input must be list!")
        if keywords:
            self.keywords = [utils.fix_unicode(k) for k in keywords[:self.configs.MAX_KEYWORDS]]

    def set_authors(self, authors):
        """
        authors are in ["firstName lastName", "firstName lastName"] format
        """
        if not isinstance(authors, list):
            raise Exception("authors input must be list!")
        if authors:
            authors = authors[:self.configs.MAX_AUTHORS]
            self.authors = [utils.fix_unicode(author) for author in authors]

    def set_summary(self, summary):
        """
        summary is a paragraph of text from the title + body text
        """
        summary = summary[:self.configs.MAX_SUMMARY]
        self.summary = utils.fix_unicode(summary)
