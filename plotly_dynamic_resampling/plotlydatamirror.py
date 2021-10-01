# -*- coding: utf-8 -*-
"""
Wrapper around the plotly figure to allow bookkeeping and back-end based resampling of
HF data.

Future work:
    * Add functionality to let the user define a downsampling method

"""
__author__ = "Jonas Van Der Donckt, Emiel Deprost"

from typing import List, Optional, Union, Iterable

import pandas as pd
import plotly.graph_objects as go

import dash_bootstrap_components as dbc
import dash_core_components as dcc
from jupyter_dash import JupyterDash
from dash.dependencies import Input, Output, State
from uuid import uuid4
import re


class PlotlyDataMirror(go.Figure):
    """Mirrors the figures' `data` attribute to allow resampling on the back-end."""

    def __init__(
            self,
            figure: go.Figure,
            global_n_shown_samples: int = 1000,
            verbose: bool = False
    ):
        """Instantiate a data mirror.

        Parameters
        ----------
        figure: go.Figure
            The figure that will be decorated.
        global_n_shown_samples: int, optional
            The global set number of samples that will be shown for each trace.
            by default 1000.
        verbose: bool, optional
            Whether some verbose messages will be printed or not, by default False
        """
        self._hf_data: List[dict] = []
        self._global_n_shown_samples = global_n_shown_samples
        self._print_verbose = verbose
        self._downsampler = None  # downsampling method, still to be implemented

        super().__init__(figure)

    def _query_hf_data(self, trace: dict) -> Optional[dict]:
        """Query the internal `hf_data` attribute and returns a match based on `uid`.

        Parameters
        ----------
        trace : dict
            The trace where we want to find a match for.

        Returns
        -------
        Optional[dict]
            The `hf_data`-trace dict if a match is found, else `None`.

        """
        for trace_data in self._hf_data:
            if trace_data['uid'] == trace['uid']:
                return trace_data

        trace_props = {
            k: trace[k]
            for k in set(trace.keys()).difference({'x', 'y'})
        }
        self._print(f"[W] trace with {trace_props} not found")
        return None

    def _print(self, *values):
        """Helper method for printing if `verbose` is set to True"""
        if self._print_verbose:
            print(*values)

    def check_update_trace_data(self, trace, t_start=None, t_stop=None):
        """Check and updates the passed`trace`.

        Note
        ----
        This is a pass by reference. The passed trace object will be updated.
        No new view of this trace will be created!

        Parameters
        ----------
        trace : BaseTraceType or dict
             - An instances of a trace class from the plotly.graph_objs
                package (e.g plotly.graph_objs.Scatter, plotly.graph_objs.Bar)
              - or a dicts where:

                  - The 'type' property specifies the trace type (e.g.
                    'scatter', 'bar', 'area', etc.). If the dict has no 'type'
                    property then 'scatter' is assumed.
                  - All remaining properties are passed to the constructor
                    of the specified trace type.
        t_start : Optional[pd.Timestamp], optional
            The start time range for which we want resampled data to be updated to,
            by default None,
        t_stop : Optional[pd.Timestamp], optional
            The end time for which we want the resampled data to be updated to,
            by default None

        """
        hf_data = self._query_hf_data(trace)
        if hf_data is not None:
            df_data = self._slice_time(hf_data['df_hf'], t_start, t_stop)
            df_res: pd.Series = self._resample_series(df_data, hf_data["max_n_samples"])
            trace["x"] = df_res.index
            trace["y"] = df_res.values
        else:
            self._print('hf_data not found')

    def check_update_figure_dict(
            self,
            figure: dict,
            t_start: Optional[pd.Timestamp] = None,
            t_stop: Optional[pd.Timestamp] = None,
            xaxis: str = None,
    ):
        """Check and update the traces within the figure dict.

        This method will most likely be used within a `Dash` callback to resample the
        view, based on the configured number of parameters.

        Note
        ----
        This is a pass by reference. The passed trace object will be updated.
        No new view of this trace will be created!

        Parameters
        ----------
        figure : dict
            The figure dict
        t_start : Optional[pd.Timestamp], optional
            The start time range for which we want resampled data to be updated to,
            by default None,
        t_stop : Optional[pd.Timestamp], optional
            The end time for which we want the resampled data to be updated to,
            by default None
        xaxis: str, Optional
            Additional trace-update filter
        """
        for trace in figure["data"]:
            if xaxis is not None:
                # we skip when:
                # * the change was made on the first row and the trace its xaxis is not
                #   in [None, 'x']
                #    -> why None: traces without row/col argument stand on first row and
                #      d do not have the xaxis property
                # * xaxis != trace['xaxis'] for NON first rows
                if ((xaxis == 'x' and trace.get("xaxis", None) not in [None, 'x']) or
                    (xaxis != 'x' and trace.get('xaxis', None) != xaxis)):
                    continue
            self.check_update_trace_data(trace=trace, t_start=t_start, t_stop=t_stop)

    @staticmethod
    def _slice_time(
            df_data: pd.Series,
            t_start: Optional[pd.Timestamp] = None,
            t_stop: Optional[pd.Timestamp] = None,
    ) -> pd.Series:
        def to_same_tz(
                ts: Union[pd.Timestamp, None],
                reference_tz=df_data.index.tz
        ) -> Union[pd.Timestamp, None]:
            if ts is None:
                return None
            elif reference_tz is not None:
                if ts.tz is not None:
                    assert ts.tz.zone == reference_tz.zone
                    return ts
                else:  # localize -> time remains the same
                    return ts.tz_localize(reference_tz)
            elif reference_tz is None and ts.tz is not None:
                return ts.tz_localize(None)
            return ts

        return df_data[to_same_tz(t_start):to_same_tz(t_stop)]

    @staticmethod
    def _resample_series(
            df_data: pd.Series,
            max_n_samples,
    ) -> pd.Series:
        df_res = df_data[:: (max(1, len(df_data) // max_n_samples))]
        # ------- add None where there are gaps / irregularly sampled data
        tot_diff_sec_series = df_res.index.to_series().diff().dt.total_seconds()

        # use a quantile based approach
        max_gap_q_s = tot_diff_sec_series.quantile(0.95)

        # add None data-points in between the gaps
        df_res_gap = df_res.loc[tot_diff_sec_series > max_gap_q_s].copy()
        df_res_gap.loc[:] = None
        df_res_gap.index -= pd.Timedelta(microseconds=1)
        index_name = df_res.index.name
        df_res = pd.concat(
            [df_res.reset_index(drop=False), df_res_gap.reset_index(drop=False)]
        ).set_index(index_name).sort_index()
        return df_res['data']

    def add_trace(
            self,
            trace,
            # Use this if you have high-dimensional data
            orig_x: Optional[pd.DatetimeIndex] = None,
            orig_y: Iterable = None,
            max_n_samples: int = None,
            cut_points_to_view: bool = False,
            **trace_kwargs
    ):
        """Add a trace to the figure.

        Note
        ----
        As constructing traces with high dimensional data really takes a
        long time -> it is preferred to just create an empty trace and pass the
        high dimensional to this method, using the `orig_x` and `orig_y` parameters.
        >>> from plotly.subplots import make_subplots
        >>> df = pd.DataFrame()  # a high-dimensional dataframe
        >>> fig = PlotlyDataMirror(make_subplots(...))
        >>> fig.add_trace(go.Scattergl(x=[], y=[], ...), orig_x=df.index, orig_y=.df['c'])

        Note
        ----
        Sparse time-series data (e.g., a scatter of detected peaks), can hinder the
        the automatic-zoom functionality; as these will not be stored in the data-mirror
        and thus not be (re)sampled to the view. To circumvent this, the
        `cut_points_to_view` argument can be set, which forces these sparse data-series
        to be also stored in the database.

        Note
        ----
        `orig_x` and `orig_y` have priority over the trace's data.

        Parameters
        ----------
        trace : BaseTraceType or dict
            Either:
              - An instances of a trace classe from the plotly.graph_objs
                package (e.g plotly.graph_objs.Scatter, plotly.graph_objs.Bar)
              - or a dicts where:

                  - The 'type' property specifies the trace type (e.g.
                    'scatter', 'bar', 'area', etc.). If the dict has no 'type'
                    property then 'scatter' is assumed.
                  - All remaining properties are passed to the constructor
                    of the specified trace type.
        orig_x: pd.Series, optional
            The original high frequency time-index. If set, this has priority over the
            trace's data.
        orig_y: pd.Series, optional
            The original high frequency values. If set, this has priority over the
            trace's data.
        max_n_samples : int, optional
            The maximum number of samples that will be shown by the trace.\n
            .. note::
                If this variable is not set; `_global_n_shown_samples` will be used.
        cut_points_to_view: boolean, optional
            If set to True and the trace it's format is a high-dimensional trace type,
            then the trace's datapoints will be cut to the corresponding front-end view,
            even if the total number of samples is lower than the MAX amount of samples.
        **trace_kwargs:
            Additional trace related keyword arguments
            e.g.: row=.., col=..., secondary_y=...,
            see trace_docs: https://plotly.com/python-api-reference/generated/plotly.graph_objects.Figure.html#plotly.graph_objects.Figure.add_traces

        Returns
        -------
        BaseFigure
            The Figure that add_trace was called on

        """
        if max_n_samples is None:
            max_n_samples = self._global_n_shown_samples

        # first add the trace, as each (even the non hf data traces), must contain this
        # key for comparison
        trace.uid = str(uuid4())

        high_dimensional_traces = ["scatter", "scattergl"]
        if trace["type"].lower() in high_dimensional_traces:
            orig_x = trace["x"] if orig_x is None else orig_x
            orig_y = trace["y"] if orig_y is None else orig_y

            assert len(orig_x) > 0
            assert len(orig_x) == len(orig_y)

            numb_samples = len(orig_x)
            # these traces will determine the autoscale
            #   -> so also store when cut_points_to_view` is set.
            if numb_samples > max_n_samples or cut_points_to_view:
                self._print(
                    f"[i] resample {trace['name']} - {numb_samples}->{max_n_samples}")

                # we will re-create this each time as df_hf withholds
                df_hf = pd.Series(data=orig_y, index=pd.to_datetime(orig_x), copy=False)
                df_hf.rename('data', inplace=True)
                df_hf.index.rename('timestamp', inplace=True)

                # Checking this now avoids less interpretable `KeyError` when resampling
                assert df_hf.index.is_monotonic_increasing
                self._hf_data.append(
                    {
                        "max_n_samples": max_n_samples,
                        "df_hf": df_hf,
                        "uid": trace.uid
                        # "resample_method": "#resample_method,
                    }
                )
                # first resample the high-dim trace b4 converting it into javascript
                self.check_update_trace_data(trace)
                super().add_trace(trace=trace, **trace_kwargs)
            else:
                self._print(
                    f"[i] NOT resampling {trace['name']} - {numb_samples} samples")
                trace.x = orig_x
                trace.y = orig_y
                return super().add_trace(trace=trace, **trace_kwargs)
        else:
            self._print(f"trace {trace['type']} is not a high-dimensional trace")

            # orig_x and orig_y have priority over the traces' data
            trace["x"] = trace["x"] if orig_x is not None else orig_x
            trace["y"] = trace["y"] if orig_y is not None else orig_y
            assert len(trace["x"]) > 0
            assert len(trace["x"] == len(trace["y"]))
            return super().add_trace(trace=trace, **trace_kwargs)

    def show_dash(self, mode=None, **kwargs):
        app = JupyterDash("local_app")
        app.layout = dbc.Container(dcc.Graph(id="resampled-graph", figure=self))

        @app.callback(
            Output("resampled-graph", "figure"),
            Input("resampled-graph", "relayoutData"),
            State("resampled-graph", "figure")
        )
        def update_graph(changed_layout: dict, current_graph):
            if changed_layout:
                self._print("-" * 100 + "\n", "changed layout", changed_layout)

                # determine the start and end regex matches
                def get_matches(regex: re.Pattern, strings: Iterable[str]) -> List[str]:
                    matches = []
                    for item in strings:
                        m = regex.match(item)
                        if m is not None:
                            matches.append(m.string)
                    return sorted(matches)

                key_list = changed_layout.keys()
                start_matches = get_matches(re.compile(r'xaxis\d*.range\[0]'), key_list)
                stop_matches = get_matches(re.compile(r'xaxis\d*.range\[1]'), key_list)
                if len(start_matches) and len(stop_matches):
                    for t_start_key, t_stop_key in zip(start_matches, stop_matches):
                        # check if the xaxis<NUMB> part of xaxis<NUMB>.[0-1] matches
                        assert t_start_key.split('.')[0] == t_stop_key.split('.')[0]
                        self.check_update_figure_dict(
                            current_graph,
                            t_start=pd.to_datetime(changed_layout[t_start_key]),
                            t_stop=pd.to_datetime(changed_layout[t_stop_key]),
                            xaxis='x' + t_start_key.split('.')[0][5:]
                        )
                elif len(get_matches(re.compile(r'xaxis\d*.autorange'), key_list)):
                    # Autorange is applied on all axes -> hence no xaxis argument
                    self.check_update_figure_dict(current_graph)
            return current_graph

        app.run_server(mode=mode, **kwargs)
