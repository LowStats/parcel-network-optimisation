## **Research Components**

**For section 3 and 4, we can decide to do only one if we do not have time to do both. I prefer to include Section 4 \- Irfan**


### **1\. Spatial Scoping & Data Collection (GIS & Open Data)**

* **Study Boundary:** Clearly demarcate the geographic polygon of **Kennedy Town** using Hong Kong Planning Department Tertiary Planning Units or Street Blocks to prevent edge-effect distortions.  
* Use **Street Blocks or Representative Residential Buildings/Clusters** as the demand nodes (so that the 300m / 5-min walking matrix reflects realistic urban micro-geography)   
* Allocate population/parcel demand by combining district/TPU/sub-unit census benchmarks with building/block counts or residential floor/unit estimates.  
*  We will explicitly state this allocation assumption in the methodology.  Something like "We use street-block / residential cluster nodes rather than coarse TPUs to preserve 300m walking-threshold accuracy and avoid aggregation bias, while anchoring total demand to official Census figures."  
* **Demand Nodes (**$i\in I$**):**  
  * 30–50 major residential estates, public housing blocks, and high-density tenements (e.g  The Merton, Belcher’s, Kwun Lung Lau).  
  * Extract building centroids (Latitude/Longitude) via **GeoInfo Map / CSDI (Common Spatial Data Infrastructure)** or OpenStreetMap (OSM).  
  * Estimate demand weight (${h}_{i}$) using **HK Population Census** data (household count $\times$ average daily e-commerce parcel arrival rate per household).  
* **Supply & Candidate Nodes (**$j\in J$**):**  
  * **Existing Sites:** Scrape or manually log coordinates of current **SF Express**, **iPostal Station (Hongkong Post)**, **Alfred24**, and **Cainiao** lockers.  
  * **Candidate Sites:** Identify new feasible locations (e.g., MTR exits, wet markets, shopping podiums, 24-hour convenience stores) where locker installation is realistic.  
* **Walking Distance Matrix (**${d}_{ij}$**):**  
  * Avoid straight-line (Euclidean) distances → Hong Kong’s steep terrain (e.g., Pok Fu Lam slopes, stairs, footbridges) makes Euclidean distance inaccurate.  
  * Use **OSRM (Open Source Routing Machine)** or **OSMnx (Python)** to compute actual pedestrian network walking distances and times between every demand node $i$ and candidate site $j$.

### 

### 

### **2\. Integer Linear Programming (ILP) Formulation**

 Formulate and compare two classic pick-up location models:

* **Model A: Set Covering Location Problem (SCLP)**   
  * ***Goal**: Minimize cost/number of lockers to cover 100% of residents within a 300m (\~4–5 min) walk.*  
  * **Decision Variable:** ${x}_{j}\in \{0,1\}$ (1 if a locker is placed at candidate site $j$, 0 otherwise).  
  * **Coverage Parameter:** ${a}_{ij}=1$ if walking distance ${d}_{ij}\leq 300m$, else $0$.  
  * **Objective:** $\min\limits_{}\sum\limits_{j\in J}^{}{c}_{j}{x}_{j}$ (where ${c}_{j}$ is installation/rental cost, or ${c}_{j}=1$ to minimize facility count).  
  * **Constraint:** $\sum\limits_{j\in J}^{}{a}_{ij}{x}_{j}\geq 1,\forall i\in I$ (every building has at least one accessible locker).  
* **Model B: Maximal Covering Location Problem (MCLP)**   
  * ***Goal**: Maximize covered population given a fixed budget or maximum number of lockers (*$P$*).*  
  * **Additional Variable:** ${y}_{i}\in \{0,1\}$ (1 if demand node $i$ is covered, 0 otherwise).  
  * **Objective:** $\max\limits_{}\sum\limits_{i\in I}^{}{h}_{i}{y}_{i}$  
  * **Constraints:**  
    * $\sum\limits_{j\in J}^{}{a}_{ij}{x}_{j}\geq {y}_{i},\forall i\in I$ (node $i$ is only covered if at least one nearby locker is open).  
    * $\sum\limits_{j\in J}^{}{x}_{j}\leq P$ (budget/facility limit).  
* **Implementation:** Solve both models in **Python using `PuLP` or `SciPy (milp)`** to obtain optimal locker placements and compare against the existing baseline networks (?)

### 

### **3\. Stochastic Simulation (Evaluating Overflow & Service Levels)**

Deterministic coverage models assume infinite locker capacity. In reality, peak periods (e.g., Double 11, weekends) cause locker overflow, forcing couriers to redirect parcels or delay delivery.

* **Demand Generation:** Model daily parcel arrivals at building $i$ as a **Poisson process** (${\lambda }_{i}$) or **Negative Binomial distribution** (to capture overdispersion during e-commerce sales).  
* **Locker Capacity (**${K}_{j}$**):** Assign realistic compartment counts per locker bank (e.g., small: 40 compartments, medium: 80, large: 120\) and model parcel dwell time (e.g., 60% picked up within 24 hours, 30% in 48 hours, 10% in 72 hours).  
* **Simulation Execution:** Run a **Monte Carlo discrete-event simulation (e.g., 1,000 simulated days)** in Python (`SimPy` or NumPy) on your ILP-optimized network.  
* **Key Metrics to Measure:**  
  * **Overflow Probability:** % of days a locker reaches 100% capacity.  
  * **Unmet Demand Rate:** % of parcels rejected due to full compartments.  
  * **Average Utilization Rate:** How efficiently locker space is used.

### 

### **4\. Sensitivity Analysis & Managerial Insights**

* **Walking Threshold Sensitivity:** Vary the coverage radius ${D}_{\max\limits_{}}$ from **200m to 500m** (in 50m increments) to plot the trade-off curve between customer convenience and required infrastructure cost.  
* **Budget / Facility Expansion (**$P$**):** Plot the marginal population coverage gained for each additional locker (\$P \= 1, 2, \\dots, 15\$) to identify the **point of diminishing returns** (the "elbow" of the curve).  
* **Peak Surge Stress Test:** Increase arrival rates ${\lambda }_{i}$ by \+50% and \+100% in the simulation to determine where modular locker capacity expansions are most urgently needed.

