import datetime
import ipaddress
import socket
from collections import deque
from enum import Enum
from pathlib import Path

import polars as pl
import requests
import rich_click as click
from pydantic import BaseModel
from rich.console import Console, ConsoleRenderable, Group, RichCast
from rich.progress import GetTimeCallable, Progress, ProgressColumn
from rich.table import Table
from rich.traceback import install
from validators.domain import domain as is_domain

from cfresolve.exceptions import (
    CSVReadError,
    DomainsColumnNotFoundError,
)


class ResolveResultType(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"
    NOT_RESOLVED = "not_resolved"


class ResolveResult(BaseModel):
    domain: str
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    result: ResolveResultType

    @property
    def is_ok(self) -> bool:
        return self.result == ResolveResultType.OK

    def to_rich_row(self) -> tuple[str]:
        if self.result == ResolveResultType.OK:
            return (self.domain, str(self.ip), "")
        return (self.domain, "[red]FAIL[/]", f"[red]{self.result.value}[/]")

    def csv_row(self) -> tuple[str]:
        if self.result == ResolveResultType.OK:
            return (self.domain, str(self.ip), "")
        return (self.domain, "FAIL", self.result.value)


console = Console()
dt_str = datetime.datetime.now(tz=datetime.timezone.utc).strftime(
    r"%Y%m%d_%H%M%S"
)


class TabularProgress(Progress):
    def __init__(  # noqa: PLR0913
        self,
        *columns: str | ProgressColumn,
        table_max_rows: int,
        console: Console | None = None,
        auto_refresh: bool = True,
        refresh_per_second: float = 10,
        speed_estimate_period: float = 30.0,
        transient: bool = False,
        redirect_stdout: bool = True,
        redirect_stderr: bool = True,
        get_time: GetTimeCallable | None = None,
        disable: bool = False,
        expand: bool = False,
    ) -> None:
        self.results = deque(maxlen=table_max_rows)
        self.update_table()
        super().__init__(
            *columns,
            console=console,
            auto_refresh=auto_refresh,
            refresh_per_second=refresh_per_second,
            speed_estimate_period=speed_estimate_period,
            transient=transient,
            redirect_stdout=redirect_stdout,
            redirect_stderr=redirect_stderr,
            get_time=get_time,
            disable=disable,
            expand=expand,
        )

    def update_table(self, result: tuple[str] | None = None) -> None:
        if result is not None:
            self.results.append(result)
        table = Table()
        table.add_column("Domain", width=45)
        table.add_column("IP", width=15)
        table.add_column("Note", width=15)

        for row_cells in self.results:
            table.add_row(*row_cells)

        self.table = table

    def get_renderable(self) -> ConsoleRenderable | RichCast | str:
        return Group(self.table, *self.get_renderables())


def guess_domains_col(csv_path: Path) -> str:
    lim_df = pl.scan_csv(csv_path, has_header=True, n_rows=10).collect()
    for column_name in lim_df.columns:
        if all(
            lim_df[column_name].map_elements(
                lambda s: is_domain(s) or not (s), return_dtype=pl.Boolean
            )
        ):
            return column_name
    raise DomainsColumnNotFoundError


def read_domains(csv_path: Path) -> list[str]:
    """reads domains from a csv file

    The domains should be either in the column `domains` or in the first column

    :param csv_path: path to csv file
    :type csv_path: Path
    """
    domain_col = guess_domains_col(csv_path)

    try:
        return (
            pl.scan_csv(csv_path)
            .select(domain_col)
            .collect()[domain_col]
            .to_list()
        )
    except pl.exceptions.PolarsError as e:
        msg = "Error reading csv file"
        raise CSVReadError(msg) from e


def resolve_domain(
    domain: str,
) -> ResolveResult:
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(domain))
        if ip.is_private:
            return ResolveResult(
                domain=domain, ip=None, result=ResolveResultType.BLOCKED
            )
        return ResolveResult(domain=domain, ip=ip, result=ResolveResultType.OK)
    except socket.gaierror:
        return ResolveResult(
            domain=domain, ip=None, result=ResolveResultType.NOT_RESOLVED
        )


@click.command()
@click.option(
    "--input",
    "-i",
    "domains_path",
    type=click.Path(
        exists=True, file_okay=True, dir_okay=False, readable=True
    ),
)
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(),
)
def app(domains_path: Path, output_path: Path) -> None:
    if domains_path is None:
        domains_path = Path.cwd() / "domains.csv"
        if domains_path.exists():
            console.log(f"Domains file exit: {domains_path}")
        else:
            r = requests.get(
                "https://raw.githubusercontent.com/tempookian/"
                "cfresolve/master/assets/domains.csv",
                timeout=10,
            )
            domains_path.write_text(r.text)

    if output_path is None:
        output_path = Path.cwd() / "results" / f"{dt_str}.csv"
    elif Path(output_path).exists():
        output_path = Path(output_path)
        raise click.FileError(output_path.absolute(), "File exists")
    output_path.parent.mkdir(exist_ok=True, parents=True)

    install()
    domains = read_domains(domains_path)

    progress: TabularProgress
    with TabularProgress(table_max_rows=20) as progress:
        task = progress.add_task("Ping", total=len(domains))
        for domain in domains:
            result = resolve_domain(domain)
            with output_path.open("a") as outfile:
                outfile.write(",".join(result.csv_row()))
            progress.update_table(result.to_rich_row())
            progress.update(task, advance=1)


if __name__ == "__main__":
    app()
