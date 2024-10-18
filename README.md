# sportadmin-tools
Scrape website using Selenium+Python to easily traverse login/password page and iframes.

## Setup
### Use pipenv to create a sandbox environment
    pipenv install

### Run commands inside pipenv
    pipenv run python3 sportadmin_scraper.py loginmail password
    pipenv run python3 sportadmin_analyzer.py

## Tools
### sportadmin_scaper.py
* Scrapes all "match" entries and collects all available players' data. Currently only supports "current year", other filters may also still be hardcoded.
* Stores the result in sportadmin.csv

### sportadmin_analyzer.py
Reads sportadmin.csv and presents player statistics:
* Total reported availablility?
* How many times did each player play in each team?
* Which weekends did players play multiple games?

## Output samples
```
======================================
Fördelning av spelade matcher per
serie. Varje streck är en match.

series             A1       B3      D1
player name                           
Player_01    ||||||||    |||||        
Player_41    ||||||||      |||        
Player_10    ||||||||       ||        
Player_18    ||||||||        |        
Player_20    ||||||||        |        
Player_06     |||||||    |||||        
Player_31     |||||||     ||||        
Player_43        ||||                 
Player_02         |||    |||||       |
Player_40         |||      |||       |
Player_24         |||      |||        
Player_14         |||       ||        
Player_11          ||  |||||||        
Player_04          ||    |||||       |
Player_08          ||    |||||        
Player_25          ||     ||||      ||
Player_15          ||      |||       |
Player_29          ||        |       |
Player_42          ||        |        
Player_26           |   ||||||       |
Player_07           |   ||||||        
Player_36           |       ||       |
Player_12           |        |      ||
Player_45           |        |        
Player_21           |              |||
Player_23               ||||||      ||
Player_09                   ||   |||||
Player_27                   ||   |||||
Player_05                    |   |||||
Player_03                    |    ||||
Player_19                    |    ||||
Player_17                    |     |||
Player_35                    |       |
Player_44                    |        
Player_22                       ||||||
Player_28                         ||||
Player_33                         ||||
Player_38                         ||||
Player_13                          |||
Player_16                          |||
Player_37                          |||
======================================
```

```
========================================================
Lista veckor som spelare dubblerat matcher och i vilka
serier de dubblerat vid varje tillfälle. (matcher i
andra åldersgrupper ej inräknade))

player name  count                          series_names
  Player_01      5 A1,B3 : A1,B3 : A1,B3 : A1,B3 : A1,B3
  Player_06      4         A1,B3 : A1,B3 : A1,B3 : A1,B3
  Player_31      4         A1,B3 : A1,B3 : A1,B3 : A1,B3
  Player_41      3                 A1,B3 : A1,B3 : A1,B3
  Player_10      2                         A1,B3 : A1,B3
  Player_02      1                                 B3,D1
  Player_04      1                                 B3,D1
  Player_11      1                                 A1,B3
  Player_18      1                                 A1,B3
  Player_20      1                                 A1,B3
  Player_19      1                                 B3,D1
  Player_25      1                                 B3,D1
  Player_23      1                                 B3,D1
========================================================
```

```
=====================================================================================================================================================
Fördelning av spelade matcher per serie och vecka.

week_series 33_A1 33_B3 33_D1 34_A1 34_B3 34_D1 35_A1 35_B3 35_D1 36_A1 36_B3 36_D1 37_A1 37_B3 38_A1 38_B3 38_D1 39_A1 39_B3 39_D1 40_A1 40_B3 40_D1
player name                                                                                                                                          
Player_01      A1    B3          A1    B3          A1                A1                A1    B3    A1                A1    B3          A1    B3      
Player_02            B3                B3    D1    A1                      B3                B3    A1                      B3          A1            
Player_03            B3                                        D1                D1                            D1                                  D1
Player_04            B3          A1                      B3    D1          B3                B3          B3                            A1            
Player_05            B3                      D1                                  D1                            D1                D1                D1
Player_06      A1    B3          A1                A1    B3                B3          A1    B3    A1    B3          A1                A1            
Player_07            B3                B3          A1                      B3                            B3                B3                B3      
Player_08            B3                B3          A1                                        B3          B3          A1                      B3      
Player_09            B3                      D1                D1                D1                      B3                      D1                D1
Player_10      A1    B3          A1                A1    B3          A1                A1          A1                A1                A1            
Player_11            B3                B3                B3                B3          A1                B3          A1    B3                B3      
Player_12      A1                                                          B3                                                    D1                D1
Player_13                  D1                D1                                                                                  D1                  
Player_14      A1                      B3                B3                                                          A1                A1            
Player_15                  D1    A1                      B3                B3          A1                B3                                          
Player_16                                                      D1                D1                                              D1                  
Player_17                  D1                D1                D1                                                                            B3      
Player_18      A1                A1                A1                A1                A1          A1                A1                A1    B3      
Player_19                                    D1                                  D1                      B3    D1                D1                  
Player_20      A1                A1    B3          A1                A1                A1          A1                A1                A1            
Player_21                  D1    A1                                                                                              D1                D1
Player_22                  D1                D1                D1                D1                            D1                D1                  
Player_23                  D1          B3                B3                B3                B3          B3    D1          B3                        
Player_24      A1                A1                A1                      B3                B3                                              B3      
Player_25                  D1          B3                B3    D1    A1                A1                B3                B3                        
Player_26                  D1          B3                B3          A1                      B3          B3                B3                B3      
Player_27                  D1                D1          B3                      D1                            D1                D1          B3      
Player_28                  D1                                                    D1                                              D1                D1
Player_29                                                                              A1          A1                      B3                      D1
Player_31      A1                A1    B3          A1                A1                            A1    B3          A1    B3          A1    B3      
Player_33                                    D1                D1                                              D1                D1                  
Player_35                                                                                                                  B3                      D1
Player_36                        A1                      B3                                                                B3                      D1
Player_37                                    D1                                  D1                                                                D1
Player_38                                    D1                D1                D1                                              D1                  
Player_40      A1                      B3                            A1                      B3                D1    A1                      B3      
Player_41      A1                A1                A1    B3          A1                A1    B3    A1                A1                A1    B3      
Player_42                                                            A1                      B3                                        A1            
Player_43                                                            A1                A1                            A1                A1            
Player_44                                                                  B3                                                                        
Player_45                                                                  B3                      A1                                                
=====================================================================================================================================================
```
