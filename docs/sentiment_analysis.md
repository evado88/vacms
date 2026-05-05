Create a scatter plot visualizing rumor sentiment over time.

Dataset:
- Source: CSA Daily Tracker (CSADailyTracker model)
- Include only records where rum (TR-12) indicates a rumor (values: 01, 1, yes, true)
- Each point represents a single rumor (unique key)

Axes:
- X-axis: Time in months, derived from 'today' field (fallback to 'submissiondate' or 'start')
- Y-axis: Weighted Sentiment Score computed as:
    Sentiment(desci) × log(1 + ru_num)
    where:
      - desci = rumor description (TR-14)
      - ru_num = number of people affected (TR-15)

Sentiment Analysis:
- Apply NLP sentiment scoring to 'desci' with values ranging from -1 (negative) to +1 (positive)

Visual Encoding:
- نقاط (dots): each rumor
- Color:
    - Green for positive sentiment (score > 0)
    - Red for negative sentiment (score < 0)
- Dot size proportional to ru_num (affected population)
- Add a horizontal reference line at y = 0 to separate positive and negative sentiment
- Optionally include a faint 45-degree diagonal reference line for visual guidance

Enhancements:
- Tooltip should display:
    - Rumor description (desci)
    - Source (sorce_rum / other_rum)
    - Location (province, district)
    - Affected population (ru_num)
- Enable filtering by geography and date range (last 7 days, 30 days, custom)

Output:
- Clean, interactive scatter plot suitable for dashboard embedding


Background and Rationale
The Zambia Sample-Based Registration System (SRS) is a vital initiative addressing the
country’s gap in mortality surveillance, a key component of effective public health and
development planning. Mortality data, including cause-of-death statistics, is critical for
tracking health trends, designing evidence-based interventions, and monitoring progress
toward national and global health targets, including the Sustainable Development Goals
(SDGs). Despite its importance, Zambia has faced significant challenges in mortality
surveillance, including incomplete reporting and limited integration with existing systems.
The SRS aims to generate reliable and timely mortality data by employing a community-
based approach that complements Zambia’s Civil Registration and Vital Statistics (CRVS)
system. While the CRVS system focuses on the legal documentation of births and deaths, the
SRS strengthens this framework by collecting granular mortality data, including verbal
autopsy assessments, to determine the causes of death. This initiative aligns with the African
Programme on Accelerated Improvement of Civil Registration and Vital Statistics (APAI-
CRVS) and international commitments such as the UN Legal Identity Agenda and the
WHO’s Global Health Observatory Guidelines for mortality surveillance.
The SRS leverages a hierarchical structure to ensure comprehensive data collection and
management. Community Surveillance Assistant Assistants (CSAs) identify and report vital
events, while Mortality Surveillance Officers (MSOs) conduct detailed verbal autopsies in
collaboration with CSAs, with the entire process coordinated by Regional Data Managers
(RDMs). Provincial Coordinators oversee the process, ensuring quality and consistency
across districts. The system also incorporates a civil registration notification component,
where CSAs facilitate the registration of births and deaths with the District office of National
Registration, Passports, and Citizenship (DNRPC), thereby reinforcing CRVS processes
(Figure 1).
This dual approach ensures that mortality data collected through the SRS is systematically
integrated into broader national and regional health information systems, addressing data
gaps and supporting the effective allocation of resources. By aligning with international
frameworks and best practices, the SRS strengthens Zambia’s ability to monitor and respond



to health challenges, ultimately improving the health and well-being of its population. This
document outlines the Standard Operating Procedures (SOPs) required for the effective
implementation of the SRS, providing a structured guide for data collectors and managers to
ensure consistency, accountability, and quality in the system.
