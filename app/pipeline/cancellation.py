from __future__ import annotations

from threading import Event


class PipelineCancelled(Exception):
  pass


class CancellationToken:
  def __init__(self) -> None:
    self._event = Event()

  @property
  def is_cancelled(self) -> bool:
    return self._event.is_set()

  def cancel(self) -> None:
    self._event.set()

  def check(self) -> None:
    if self._event.is_set():
      raise PipelineCancelled("Pipeline run was cancelled")

  def wait(self, seconds: float) -> None:
    if seconds > 0:
      self._event.wait(seconds)
    self.check()
