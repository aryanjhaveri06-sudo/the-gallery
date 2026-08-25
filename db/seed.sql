-- OPTIONAL demo data for The Gallery's client book.
--
-- These six collectors are the invented ones from the prototype. Load them to
-- see the CRM working, then delete them before real clients go in:
--   DELETE FROM client;   -- holdings, logs and follow-ups cascade
--
--   wrangler d1 execute gallery-crm --local  --file=db/seed.sql
--   wrangler d1 execute gallery-crm --remote --file=db/seed.sql

INSERT OR REPLACE INTO client (id,name,title,city,tier,since,lifetime_inr,focus,brief,wants,next_when,next_what,created_at,updated_at)
VALUES ('nanda','Aditi Nanda','Trustee, Nanda Family Collection','Mumbai','Principal','2014',684000000,'Moderns, trophy','Building a twenty-work museum-grade survey of the Progressives, one purchase a year, no repetition of names already held. Buys on scarcity and provenance, never on price. Will not consider anything with a restoration history.','["Gaitonde 1970s", "Tyeb diagonal", "Sher-Gil paper"]','Today, 4:30 pm','Walk her the Pundole''s Gaitonde before the catalogue goes public.','2026-08-25T00:00:00Z','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('nanda-h0','nanda','v s gaitonde','V S Gaitonde','Untitled, 1968','acq. 2019',NULL);
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('nanda-h1','nanda','s h raza','S H Raza','Bindu, 1986','acq. 2016',NULL);
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('nanda-h2','nanda','tyeb mehta','Tyeb Mehta','Untitled (Bull)','acq. 2022',NULL);
INSERT OR REPLACE INTO referral (id,client_id,name,tie) VALUES ('nanda-r0','nanda','Farhad Irani','introduced 2021, Bombay Gymkhana');
INSERT OR REPLACE INTO referral (id,client_id,name,tie) VALUES ('nanda-r1','nanda','Meher Sethi','co-trustee, Kala Ghoda Trust');
INSERT OR REPLACE INTO log (id,client_id,happened,channel,note,created_at) VALUES ('nanda-l0','nanda','2026-08-22','Call','Asked unprompted about the Gaitonde rumour. Wants first refusal, not a bidding contest.','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO log (id,client_id,happened,channel,note,created_at) VALUES ('nanda-l1','nanda','2026-08-04','Viewing','Saw the Tyeb at the office. Liked it; the scale is wrong for the Worli wall.','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO log (id,client_id,happened,channel,note,created_at) VALUES ('nanda-l2','nanda','2026-07-19','Email','Sent the 2016-2026 Gaitonde index. She forwarded it to her family office.','2026-08-25T00:00:00Z');

INSERT OR REPLACE INTO client (id,name,title,city,tier,since,lifetime_inr,focus,brief,wants,next_when,next_what,created_at,updated_at)
VALUES ('irani','Farhad Irani','Founder, Irani Capital','Mumbai','Principal','2018',312000000,'Souza, Khakhar','Collects the difficult work and enjoys the argument as much as the object. Responds to art-historical reasoning and international validation; allergic to anything that sounds like an investment pitch.','["Souza 1955-65", "Khakhar oil", "Baroda school"]','Overdue','Send the Tate Khakhar catalogue essay he asked for; then propose the London viewing.','2026-08-25T00:00:00Z','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('irani-h0','irani','f n souza','F N Souza','Head, 1962','acq. 2019',NULL);
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('irani-h1','irani','bhupen khakhar','Bhupen Khakhar','Untitled (Two Men)','acq. 2026',NULL);
INSERT OR REPLACE INTO referral (id,client_id,name,tie) VALUES ('irani-r0','irani','Aditi Nanda','introduced him to the desk');
INSERT OR REPLACE INTO log (id,client_id,happened,channel,note,created_at) VALUES ('irani-l0','irani','2026-08-19','WhatsApp','Asked for the Tate essay. Still owed.','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO followup (id,client_id,due,reason,done,created_at) VALUES ('irani-f0','irani','2026-08-19','Owed the Tate Khakhar catalogue essay, then propose the 30 Oct London viewing.',0,'2026-08-25T00:00:00Z');

INSERT OR REPLACE INTO client (id,name,title,city,tier,since,lifetime_inr,focus,brief,wants,next_when,next_what,created_at,updated_at)
VALUES ('sethi','Meher Sethi','Collector, Sethi Trust','Delhi','Senior','2016',247000000,'Raza, Husain','Decorative-led and colour-driven; buys for specific walls in the Delhi and Kasauli houses. Will pay up for red-period Raza. Needs the size conversation settled before price is discussed.','["Raza Bindu red", "Husain 1960s", "Large format"]','Overdue','She asked for a Raza under Rs 5 cr. Send the Saffronart Village au Bord comparable.','2026-08-25T00:00:00Z','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('sethi-h0','sethi','s h raza','S H Raza','Germination, 1988','acq. 2018',NULL);
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('sethi-h1','sethi','m f husain','M F Husain','Untitled (Horses)','acq. 2021',NULL);
INSERT OR REPLACE INTO referral (id,client_id,name,tie) VALUES ('sethi-r0','sethi','Aditi Nanda','Kala Ghoda Trust');
INSERT OR REPLACE INTO log (id,client_id,happened,channel,note,created_at) VALUES ('sethi-l0','sethi','2026-08-14','Call','Budget Rs 4-5 cr for the Kasauli dining room. Wants red, wants signed and dated.','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO followup (id,client_id,due,reason,done,created_at) VALUES ('sethi-f0','sethi','2026-08-14','Wants a red-period Raza under Rs 5 cr - send the Village au Bord comparable.',0,'2026-08-25T00:00:00Z');

INSERT OR REPLACE INTO client (id,name,title,city,tier,since,lifetime_inr,focus,brief,wants,next_when,next_what,created_at,updated_at)
VALUES ('chaudhri','Nikhil Chaudhri','Managing Partner, Ashwin Ventures','Bengaluru','Growth','2022',69000000,'Contemporary, first-time','Four years in, learning fast, treats the collection like a portfolio and asks for data. Best served with the compounding story and with honesty about liquidity.','["Under Rs 1 cr", "Works on paper", "Contemporary"]','Tomorrow','Send three works on paper under Rs 1 cr with five-year comparables attached.','2026-08-25T00:00:00Z','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('chaudhri-h0','chaudhri','subodh gupta','Subodh Gupta','Bucket, bronze','acq. 2026',NULL);
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('chaudhri-h1','chaudhri','f n souza','F N Souza','Crucifixion Study','acq. 2025',NULL);
INSERT OR REPLACE INTO referral (id,client_id,name,tie) VALUES ('chaudhri-r0','chaudhri','Farhad Irani','met at the Kochi preview');
INSERT OR REPLACE INTO log (id,client_id,happened,channel,note,created_at) VALUES ('chaudhri-l0','chaudhri','2026-08-21','Email','Asked how quickly he could sell if he needed to. Answer honestly - 6-9 months for paper.','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO followup (id,client_id,due,reason,done,created_at) VALUES ('chaudhri-f0','chaudhri','2026-08-25','Three works on paper under Rs 1 cr with five-year comparables attached.',0,'2026-08-25T00:00:00Z');

INSERT OR REPLACE INTO client (id,name,title,city,tier,since,lifetime_inr,focus,brief,wants,next_when,next_what,created_at,updated_at)
VALUES ('rao','Sunanda Rao','Director, Rao Foundation','Hyderabad','Senior','2019',183000000,'Contemporary, institutional','Buying toward a public foundation space opening 2028 - needs scale, names that read on a wall label, and museum-standard documentation on everything.','["Sculpture", "Institutional scale", "Khakhar"]','Fri 28 Aug','Confirm the foundation walkthrough date and send the loan-agreement template.','2026-08-25T00:00:00Z','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('rao-h0','rao','subodh gupta','Subodh Gupta','Untitled (Utensils)','acq. 2023',NULL);
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('rao-h1','rao','m f husain','M F Husain','Yatra','acq. 2026',NULL);
INSERT OR REPLACE INTO referral (id,client_id,name,tie) VALUES ('rao-r0','rao','Nikhil Chaudhri','Kochi Biennale circle');
INSERT OR REPLACE INTO log (id,client_id,happened,channel,note,created_at) VALUES ('rao-l0','rao','2026-08-18','Meeting','Foundation build slips to Q2 2028. Storage needed for two years - offer the house facility.','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO followup (id,client_id,due,reason,done,created_at) VALUES ('rao-f0','rao','2026-08-28','Confirm the foundation walkthrough date and send the loan-agreement template.',0,'2026-08-25T00:00:00Z');

INSERT OR REPLACE INTO client (id,name,title,city,tier,since,lifetime_inr,focus,brief,wants,next_when,next_what,created_at,updated_at)
VALUES ('banerjee','Rohan Banerjee','Private client, Singapore','Singapore','Growth','2023',42000000,'NRI, diaspora moderns','Non-resident buyer, so export clearance is the first question on every lot. Prefers mid-size canvases that ship well and pays in SGD - always quote landed cost, not hammer.','["Export-clear", "Mid-size canvas", "Raza"]','Mon 31 Aug','Send SGD-landed quotes for the two AstaGuru Raza lots including freight and insurance.','2026-08-25T00:00:00Z','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES ('banerjee-h0','banerjee','s h raza','S H Raza','Untitled (Rajasthan)','acq. 2025',NULL);
INSERT OR REPLACE INTO referral (id,client_id,name,tie) VALUES ('banerjee-r0','banerjee','Sunanda Rao','Singapore Art Week dinner');
INSERT OR REPLACE INTO log (id,client_id,happened,channel,note,created_at) VALUES ('banerjee-l0','banerjee','2026-08-20','Email','Confirmed SGD budget of 400k. Asked about Art Treasure restrictions - explain clearly.','2026-08-25T00:00:00Z');
INSERT OR REPLACE INTO followup (id,client_id,due,reason,done,created_at) VALUES ('banerjee-f0','banerjee','2026-08-25','SGD landed-cost quotes on the two AstaGuru Raza lots, freight and insurance included.',0,'2026-08-25T00:00:00Z');
