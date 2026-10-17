"""Extract ISTU teacher schedule cards from the university HTML page."""
from html.parser import HTMLParser


class _Node:
    def __init__(self, tag='', attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.text = []

    def has(self, name):
        return name in self.attrs.get('class', '').split()

    def descendants(self, name):
        for child in self.children:
            if child.has(name):
                yield child
            yield from child.descendants(name)

    def content(self):
        parts = list(self.text)
        for child in self.children:
            parts.append(child.content())
        return ' '.join(' '.join(parts).split())


class _Tree(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].text.append(data)


def _first(node, css_class):
    return next(node.descendants(css_class), None)


def _value(node, css_class):
    found = _first(node, css_class)
    return found.content() if found else ''


def parse_schedule(html):
    """Return a list of days, each containing nonempty lesson cards."""
    tree = _Tree()
    tree.feed(html)
    days = []
    for day in tree.root.descendants('sch-list-day'):
        header = _value(day, 'sch-list-day-header')
        date = day.attrs.get('data-params', '')
        lessons = []
        for slot in day.descendants('sch-list-item'):
            time = _value(slot, 'sch-list-item-time-inner')
            for week in slot.descendants('sch-list-item-week'):
                week_type = next((v for v in ('week-even', 'week-odd', 'week-all') if week.has(v)), 'week-all')
                for card in week.descendants('schcls-card'):
                    if card.has('schcls-empty'):
                        continue
                    title = _value(card, 'schcls-item-name')
                    if not title:
                        continue
                    lessons.append({
                        'time': time,
                        'week': week_type.removeprefix('week-'),
                        'subject': title,
                        'type': _value(card, 'schcls-item-distype'),
                        'teacher': _value(card, 'schcls-item-prepod'),
                        'groups': _value(card, 'schcls-item-group'),
                        'room': _value(card, 'schcls-item-aud'),
                    })
        if lessons:
            days.append({'day': header, 'date_params': date, 'lessons': lessons})
    return days
