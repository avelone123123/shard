# Dynamic Transfer Speed and ETA Estimation

## Mathematical Model
Throughput is calculated via moving average window:
$$\text{Speed} = \frac{\Delta \text{Bytes}}{\Delta t}$$

ETA is estimated as:
$$\text{ETA} = \frac{\text{Total} - \text{Current}}{\text{Speed}}$$
