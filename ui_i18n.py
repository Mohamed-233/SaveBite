"""Localize presentation at the UI boundary while keeping stored values stable.

This proxy delegates to Streamlit's public API. It translates labels, messages,
display formatters and table headers, never widget values, IDs or database data.
"""
import functools
import re

import streamlit as _streamlit

from i18n import get_language
from ui_messages import MESSAGES

_reverse={}
for _ar,_en in MESSAGES.items():_reverse.setdefault(_en,_ar)
_patterns=[]
for _template,_translated in MESSAGES.items():
    if re.search(r'\{p\d+\}',_template):
        _parts=re.split(r'(\{p\d+\})',_template)
        _regex=''.join('(?P<'+p[1:-1]+'>.+?)' if re.fullmatch(r'\{p\d+\}',p) else re.escape(p) for p in _parts)
        _patterns.append((len(_template),re.compile('^'+_regex+'$',re.DOTALL),_translated))
_patterns.sort(key=lambda item:item[0],reverse=True)
_prefixes=sorted([(k,v) for k,v in MESSAGES.items() if k.endswith(': ')],key=lambda pair:len(pair[0]),reverse=True)


def msg(value, *, language=None):
    if not isinstance(value,str):return value
    if (language or get_language())=='ar':return _reverse.get(value,value)
    if value in MESSAGES:return MESSAGES[value]
    if not re.search('[\u0600-\u06ff]',value):return value
    for _,pattern,translated in _patterns:
        match=pattern.fullmatch(value)
        if match:return translated.format_map({key:MESSAGES.get(item,item) for key,item in match.groupdict().items()})
    for prefix,translated in _prefixes:
        if value.startswith(prefix):return translated+value[len(prefix):]
    return value


def _table(value):
    if isinstance(value,list):return [_table(v) for v in value]
    if isinstance(value,tuple):return tuple(_table(v) for v in value)
    if isinstance(value,dict):return {msg(k):_table(v) for k,v in value.items()}
    # Translate known status/category cells, preserving user-entered names/notes.
    if isinstance(value,str):
        return MESSAGES.get(value,value) if get_language()=='en' else _reverse.get(value,value)
    return value


_TEXT_FIRST={'title','header','subheader','caption','markdown','text','info','success','warning','error','exception',
    'toast','button','checkbox','toggle','radio','selectbox','multiselect','text_input','text_area','number_input','date_input',
    'time_input','form_submit_button','expander','spinner','metric','page_link','download_button'}
_WIDGETS={'radio','selectbox','multiselect'}
_CONTAINERS={'container','empty','expander','form'}


class LocalizedStreamlit:
    def __init__(self,target):self._target=target
    def __enter__(self):
        self._target.__enter__()
        return self
    def __exit__(self,*args):return self._target.__exit__(*args)
    def __getattr__(self,name):
        original=getattr(self._target,name)
        if name=='sidebar':return LocalizedStreamlit(original)
        if not callable(original):return original
        localized=_TEXT_FIRST|_CONTAINERS|{'columns','tabs','write','dataframe','json','Page','navigation','set_page_config'}
        if name not in localized:return original
        @functools.wraps(original)
        def call(*args,**kwargs):
            args=list(args)
            if name in _TEXT_FIRST and args:
                # page_link's first positional argument is the page reference.
                if name!='page_link':args[0]=msg(args[0])
            if name=='write':args=[msg(a) if isinstance(a,str) else a for a in args]
            if name=='metric':
                if len(args)>1:args[1]=msg(args[1])
                if 'value' in kwargs:kwargs['value']=msg(kwargs['value'])
            if name in {'dataframe','json'} and args:args[0]=_table(args[0])
            if name=='tabs' and args:args[0]=[msg(v) for v in args[0]]
            if name=='navigation' and args and isinstance(args[0],dict):
                args[0]={msg(k):v for k,v in args[0].items()}
            for key in ('label','placeholder','help','caption','title','page_title'):
                if key in kwargs:kwargs[key]=msg(kwargs[key])
            if name in _WIDGETS:
                key=kwargs.get('key')
                options=kwargs.get('options',args[1] if len(args)>1 else ())
                language=get_language()
                memory='_ui_value_'+key if key else None
                locale='_ui_locale_'+key if key else None
                if (key and memory in _streamlit.session_state
                        and _streamlit.session_state.get(locale)!=language):
                    saved=_streamlit.session_state[memory]
                    valid=(saved is None or saved in options) if name!='multiselect' else all(v in options for v in saved)
                    if valid:_streamlit.session_state[key]=saved
                if key and key in _streamlit.session_state:
                    current=_streamlit.session_state[key]
                    valid=(current is None or current in options) if name!='multiselect' else all(v in options for v in current)
                    if valid:
                        # Formatting options in another language must not
                        # replace their canonical stored identifiers.
                        _streamlit.session_state[key]=current
                formatter=kwargs.get('format_func',str)
                kwargs['format_func']=lambda option:msg(formatter(option),language=language)
            result=original(*args,**kwargs)
            if name in _WIDGETS and key:
                _streamlit.session_state[memory]=result
                _streamlit.session_state[locale]=language
            if name in {'columns','tabs'}:return [LocalizedStreamlit(v) for v in result]
            if name in _CONTAINERS:return LocalizedStreamlit(result)
            return result
        return call


st=LocalizedStreamlit(_streamlit)
