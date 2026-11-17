from abc import ABC, abstractmethod


class Translator(ABC):
    @abstractmethod
    def models(self): ...

    @abstractmethod
    def translate(self, text, target, context=(), model=None, token_callback=None): ...
